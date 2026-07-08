"""Provisioning helpers imported by ``scripts/postprovision.py``.

Two idempotent steps run after ``azd provision``:
  * :func:`build_search_index` — (re)create the Azure AI Search index over the KB
    (vector + keyword/semantic fields), chunk + embed the KB docs and upload them.
  * :func:`create_foundry_agents` — create/refresh the orchestrator, triage and
    incident agents in the Foundry project and persist their IDs via ``azd env set``.

All Azure SDK imports are deferred into the functions so this module stays
importable in mock mode / CI where those libraries (and Azure itself) are absent.
"""

from __future__ import annotations

import subprocess

from .kb import chunk_doc, load_local_kb
from .prompts import (
    INCIDENT_INSTRUCTIONS,
    ORCHESTRATOR_INSTRUCTIONS,
    TRIAGE_INSTRUCTIONS,
)

# Dimensions for text-embedding-3-* small/large default to 1536/3072; the small
# model (1536) is the accelerator default. Overridable via the search field.
_EMBEDDING_DIMENSIONS = 1536


def _log(msg: str) -> None:
    print(f"[setup] {msg}")


# ---------------------------------------------------------------------------
# STEP 2 — AI Search index
# ---------------------------------------------------------------------------
def _build_index_definition(index_name: str):
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
        SimpleField(
            name="assignment_group",
            type=SearchFieldDataType.String,
            filterable=True,
            facetable=True,
        ),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=_EMBEDDING_DIMENSIONS,
            vector_search_profile_name="kb-hnsw-profile",
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="kb-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="kb-hnsw-profile", algorithm_configuration_name="kb-hnsw"
            )
        ],
    )
    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name="kb-semantic",
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ]
    )
    return SearchIndex(
        name=index_name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )


def build_search_index(
    *,
    search_endpoint: str,
    index_name: str,
    embedding_deployment: str,
) -> None:
    """Create/refresh the KB search index and upload embedded chunks. Idempotent."""
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    from ..shared import get_credential
    from .embeddings import embed_texts

    credential = get_credential()

    index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)
    index = _build_index_definition(index_name)
    index_client.create_or_update_index(index)  # idempotent
    _log(f"index '{index_name}' created/updated on {search_endpoint}")

    docs = load_local_kb()
    payload: list[dict] = []
    for doc in docs:
        chunks = chunk_doc(doc)
        vectors = embed_texts(chunks, embedding_deployment)
        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            payload.append(
                {
                    "id": f"{doc.doc_id}-{i}",
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "source": doc.source,
                    "assignment_group": doc.assignment_group,
                    "content": chunk,
                    "content_vector": vector,
                }
            )

    search_client = SearchClient(
        endpoint=search_endpoint, index_name=index_name, credential=credential
    )
    # mergeOrUpload keyed on stable ids => idempotent re-runs.
    search_client.merge_or_upload_documents(documents=payload)
    _log(f"uploaded {len(payload)} chunks from {len(docs)} KB docs")


# ---------------------------------------------------------------------------
# STEP 3 — Foundry agents
# ---------------------------------------------------------------------------
_AGENT_SPECS = [
    ("it-helpdesk-triage", TRIAGE_INSTRUCTIONS),
    ("it-helpdesk-incident", INCIDENT_INSTRUCTIONS),
    ("it-helpdesk-orchestrator", ORCHESTRATOR_INSTRUCTIONS),
]

_AGENT_ID_ENV = {
    "it-helpdesk-orchestrator": "AZURE_AI_ORCHESTRATOR_AGENT_ID",
    "it-helpdesk-triage": "AZURE_AI_TRIAGE_AGENT_ID",
    "it-helpdesk-incident": "AZURE_AI_INCIDENT_AGENT_ID",
}


def _azd_env_set(name: str, value: str) -> None:
    try:
        subprocess.run(["azd", "env", "set", name, value], check=True)
        _log(f"azd env set {name}={value}")
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
        _log(f"WARNING: could not persist {name} via azd ({exc}); set it manually.")


def create_foundry_agents(
    *,
    project_endpoint: str,
    chat_deployment: str,
) -> dict[str, str]:
    """Create/refresh the 3 Foundry agents and persist their IDs. Idempotent."""
    from azure.ai.projects import AIProjectClient

    from ..shared import get_credential

    project = AIProjectClient(endpoint=project_endpoint, credential=get_credential())
    agents_client = project.agents

    # Idempotency: index existing agents by name.
    existing: dict[str, str] = {}
    try:
        for agent in agents_client.list_agents():
            name = getattr(agent, "name", None)
            if name:
                existing[name] = agent.id
    except Exception as exc:  # pragma: no cover - live-only
        _log(f"WARNING: could not list existing agents ({exc}); creating fresh.")

    ids: dict[str, str] = {}
    for name, instructions in _AGENT_SPECS:
        if name in existing:
            agent_id = existing[name]
            try:
                agents_client.update_agent(
                    agent_id, instructions=instructions, model=chat_deployment
                )
            except Exception as exc:  # pragma: no cover - live-only
                _log(f"WARNING: update of {name} failed ({exc})")
            _log(f"agent '{name}' already exists -> {agent_id} (updated)")
        else:
            agent = agents_client.create_agent(
                model=chat_deployment, name=name, instructions=instructions
            )
            agent_id = agent.id
            _log(f"created agent '{name}' -> {agent_id}")
        ids[name] = agent_id
        _azd_env_set(_AGENT_ID_ENV[name], agent_id)

    return ids
