"""Provisioning helpers imported by ``scripts/postprovision.py``.

Two idempotent steps run after ``azd provision``:
  * :func:`build_search_index` — (re)create the Azure AI Search index over the KB
    (vector + keyword/semantic fields), chunk + embed the KB docs and upload them.
  * :func:`create_foundry_agents` — create/refresh the triage and incident
    Prompt Agents in the Foundry project (new Foundry Agent experience, via
    ``AIProjectClient.agents.create_version``) and persist their IDs via ``azd env set``.

All Azure SDK imports are deferred into the functions so this module stays
importable in mock mode / CI where those libraries (and Azure itself) are absent.
"""

from __future__ import annotations

import os
import subprocess

from .embeddings import EMBEDDING_DIMENSIONS
from .kb import chunk_doc, load_local_kb

def _log(msg: str) -> None:
    print(f"[setup] {msg}")


# ---------------------------------------------------------------------------
# STEP 2 — AI Search index
# ---------------------------------------------------------------------------
def _build_index_definition(
    index_name: str,
    *,
    openai_endpoint: str | None = None,
    embedding_deployment: str | None = None,
):
    from azure.search.documents.indexes.models import (
        AzureOpenAIVectorizer,
        AzureOpenAIVectorizerParameters,
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
        SearchableField(name="resolution_steps", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name="kb-hnsw-profile",
        ),
    ]
    # Integrated vectorizer: lets the native Foundry AI Search Knowledge tool
    # embed the query text at search time (required for vector_semantic_hybrid).
    # Authenticates as the Search service's system-assigned managed identity
    # (auth_identity=None + no api_key) — the MI is granted "Cognitive Services
    # OpenAI User" on the Foundry account.
    vectorizer_name = "kb-openai-vectorizer"
    vectorizers = None
    if openai_endpoint and embedding_deployment:
        vectorizers = [
            AzureOpenAIVectorizer(
                vectorizer_name=vectorizer_name,
                parameters=AzureOpenAIVectorizerParameters(
                    resource_url=openai_endpoint,
                    deployment_name=embedding_deployment,
                    model_name="text-embedding-3-large",
                    auth_identity=None,
                ),
            )
        ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="kb-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="kb-hnsw-profile",
                algorithm_configuration_name="kb-hnsw",
                vectorizer_name=vectorizer_name if vectorizers else None,
            )
        ],
        vectorizers=vectorizers,
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


def _verify_upload_results(results) -> None:
    """Raise if Azure AI Search reports any failed document upload."""
    for result in results or []:
        succeeded = getattr(result, "succeeded", None)
        if succeeded is None and isinstance(result, dict):
            succeeded = result.get("succeeded")
        if succeeded is not False:
            continue

        key = getattr(result, "key", None)
        error = getattr(result, "error_message", None)
        if isinstance(result, dict):
            key = key or result.get("key")
            error = error or result.get("errorMessage") or result.get("error_message")
        detail = f"document {key!r}" if key else "a document"
        raise RuntimeError(f"Azure AI Search failed to upload {detail}: {error or result!r}")


def build_search_index(
    *,
    search_endpoint: str,
    index_name: str,
    embedding_deployment: str,
    openai_endpoint: str | None = None,
) -> None:
    """Create/refresh the KB search index and upload embedded chunks. Idempotent."""
    from azure.search.documents import SearchClient
    from azure.search.documents.indexes import SearchIndexClient

    from ..shared import get_credential
    from .embeddings import embed_texts

    credential = get_credential()

    index_client = SearchIndexClient(endpoint=search_endpoint, credential=credential)
    index = _build_index_definition(
        index_name,
        openai_endpoint=openai_endpoint,
        embedding_deployment=embedding_deployment,
    )
    index_client.create_or_update_index(index)  # idempotent
    _log(f"index '{index_name}' created/updated on {search_endpoint}")

    docs = load_local_kb()
    payload: list[dict] = []
    for doc in docs:
        chunks = chunk_doc(doc)
        vectors = embed_texts(chunks, embedding_deployment, dimensions=EMBEDDING_DIMENSIONS)
        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            payload.append(
                {
                    "id": f"{doc.doc_id}-{i}",
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "source": doc.source,
                    "assignment_group": doc.assignment_group,
                    "content": chunk,
                    "resolution_steps": doc.resolution_steps,
                    "content_vector": vector,
                }
            )

    search_client = SearchClient(
        endpoint=search_endpoint, index_name=index_name, credential=credential
    )
    # mergeOrUpload keyed on stable ids => idempotent re-runs.
    results = search_client.merge_or_upload_documents(documents=payload)
    _verify_upload_results(results)
    _log(f"uploaded {len(payload)} chunks from {len(docs)} KB docs")


# ---------------------------------------------------------------------------
# STEP 3 — Foundry agents
# ---------------------------------------------------------------------------
_AGENT_NAMES = ("it-helpdesk-triage", "it-helpdesk-incident")

_AGENT_ID_ENV = {
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
    search_endpoint: str,
    search_index_name: str,
    apim_mcp_url: str,
    mcp_connection_id: str,
) -> dict[str, str]:
    """Create/refresh the triage + incident Prompt Agents and persist IDs.

    Uses the **new** Azure AI Foundry Agent experience: agents are versioned
    *Prompt Agents* created through ``AIProjectClient.agents.create_version`` on
    the ``{endpoint}/api/projects/...`` resource (data-plane v1). This is the path
    that surfaces the agents in the new Foundry portal, unlike the legacy
    ``azure.ai.agents.AgentsClient`` assistants API (``asst_``-prefixed IDs) it
    replaces.

    The canonical identifier of a new-experience agent is its **name** (equal to
    ``AgentDetails.id``); that stable name is what we persist and what the runtime
    references. ``create_version`` is idempotent-friendly: re-running publishes a
    new version of the same named agent rather than duplicating it.

    The custom Orchestrator is intentionally not created here in Phase 1; Phase 2
    will publish it as a Microsoft Agent Framework Hosted Agent.
    """
    from azure.ai.projects import AIProjectClient

    from ..shared import get_credential
    from .definitions.incident_agent import (
        INCIDENT_INSTRUCTIONS,
        build_incident_definition,
    )
    from .definitions.triage_agent import (
        build_triage_definition,
        ensure_kb_index,
        ensure_search_connection,
    )

    if not INCIDENT_INSTRUCTIONS:
        raise RuntimeError("Incident Prompt Agent instructions must not be empty.")

    ids: dict[str, str] = {}
    with AIProjectClient(endpoint=project_endpoint, credential=get_credential()) as project:
        # Idempotency: which named agents already exist (for accurate logging).
        existing: set[str] = set()
        try:
            for agent in project.agents.list():
                name = getattr(agent, "name", None)
                if name:
                    existing.add(name)
        except Exception as exc:  # pragma: no cover - live-only
            _log(f"WARNING: could not list existing agents ({exc}); creating fresh.")

        search_connection_name = ensure_search_connection(project, search_endpoint=search_endpoint)
        # Register the Search index as a Foundry Knowledge base (managed Index) so
        # the triage agent grounds via a Knowledge base, not an inline search tool.
        kb_index_asset_id = ensure_kb_index(
            project,
            connection_name=search_connection_name,
            index_name=search_index_name,
        )
        _log(f"knowledge base index ready -> {kb_index_asset_id}")

        definitions = {
            "it-helpdesk-triage": build_triage_definition(
                chat_deployment=chat_deployment,
                index_asset_id=kb_index_asset_id,
            ),
            "it-helpdesk-incident": build_incident_definition(
                chat_deployment=chat_deployment,
                apim_mcp_url=apim_mcp_url,
                mcp_connection_id=mcp_connection_id,
            ),
        }

        for name in _AGENT_NAMES:
            version = project.agents.create_version(
                agent_name=name,
                definition=definitions[name],
            )
            # AgentVersionDetails.name is the stable agent id (== AgentDetails.id).
            agent_id = getattr(version, "name", None) or name
            revision = getattr(version, "version", None)
            if name in existing:
                _log(f"agent '{name}' already exists -> {agent_id} (published v{revision})")
            else:
                _log(f"created agent '{name}' -> {agent_id} (v{revision})")
            ids[name] = agent_id
            _azd_env_set(_AGENT_ID_ENV[name], agent_id)

    return ids


# ---------------------------------------------------------------------------
# STEP 4 — Foundry **Hosted Agent** orchestrator (Microsoft Agent Framework)
# ---------------------------------------------------------------------------
_ORCHESTRATOR_NAME = "it-helpdesk-orchestrator"

# The Foundry ingress protocol + version the ResponsesHostServer speaks. The
# version string is a Foundry contract; override via env if the platform pins a
# different one (discovered on first live deploy).
_RESPONSES_PROTOCOL = "responses"
_DEFAULT_RESPONSES_VERSION = "2.0.0"


def create_hosted_orchestrator(
    *,
    project_endpoint: str,
    chat_deployment: str,
    image: str,
    cpu: str = "1",
    memory: str = "2Gi",
    responses_version: str | None = None,
) -> str:
    """Register the MAF orchestrator container as a Foundry **Hosted Agent**.

    The image is built + pushed server-side by the postprovision shell hook
    (``az acr build`` — no local Docker) and passed in as ``image``. We register
    it via the **public, stable** ``AIProjectClient.agents.create_version`` API
    with a :class:`HostedAgentDefinition` using ``container_configuration`` (the
    code-ZIP path in azure-ai-projects 2.3.0 is only a private method).

    ``create_version`` is idempotent-friendly: re-running publishes a new version
    of the same named agent. Foundry **reserves** and auto-injects all ``FOUNDRY_*``
    and ``AGENT_*`` environment variables (including ``FOUNDRY_PROJECT_ENDPOINT``),
    so we must NOT set them here — the registration API rejects reserved keys. We
    only pass the non-reserved vars the container needs (the model deployment and
    the sub-agent names).
    """
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import (
        ContainerConfiguration,
        HostedAgentDefinition,
        ProtocolVersionRecord,
    )

    from ..shared import get_credential

    version = (
        responses_version
        or os.environ.get("FOUNDRY_RESPONSES_PROTOCOL_VERSION")
        or _DEFAULT_RESPONSES_VERSION
    )
    # NOTE: FOUNDRY_* and AGENT_* are reserved for platform use and injected by
    # Foundry at run time — passing them here fails registration with
    # "invalid_payload ... reserved for platform use". main.py reads the
    # platform-injected FOUNDRY_PROJECT_ENDPOINT for the project endpoint.
    environment_variables = {
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": chat_deployment,
        "TRIAGE_AGENT_NAME": _AGENT_NAMES[0],
        "INCIDENT_AGENT_NAME": _AGENT_NAMES[1],
    }
    definition = HostedAgentDefinition(
        cpu=cpu,
        memory=memory,
        environment_variables=environment_variables,
        container_configuration=ContainerConfiguration(image=image),
        protocol_versions=[
            ProtocolVersionRecord(protocol=_RESPONSES_PROTOCOL, version=version)
        ],
    )

    with AIProjectClient(endpoint=project_endpoint, credential=get_credential()) as project:
        created = project.agents.create_version(
            agent_name=_ORCHESTRATOR_NAME,
            definition=definition,
        )
        agent_id = getattr(created, "name", None) or _ORCHESTRATOR_NAME
        revision = getattr(created, "version", None)
        _log(
            f"registered hosted orchestrator '{_ORCHESTRATOR_NAME}' -> {agent_id} "
            f"(v{revision}, image {image})"
        )

    _azd_env_set("AZURE_AI_ORCHESTRATOR_AGENT_ID", agent_id)
    return agent_id
