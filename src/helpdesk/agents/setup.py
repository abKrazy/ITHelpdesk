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
import time
from datetime import timedelta
from pathlib import Path

from .embeddings import EMBEDDING_DIMENSIONS
from .kb import KbDoc, parse_markdown

def _log(msg: str) -> None:
    print(f"[setup] {msg}")


# ---------------------------------------------------------------------------
# Data-plane resilience helpers
#
# postprovision runs from the DEPLOYER'S machine against public data-plane
# endpoints (AI Search, Storage) using the deployer's `az login` identity. Two
# environmental failure modes are common and are NOT code bugs:
#   * 401/403 while the data-plane role assignment (created during `azd provision`)
#     is still propagating — transient, clears within a few minutes → retry.
#   * connect timeout because a governed subscription's Azure Policy disabled
#     public network access on the endpoint — the laptop simply cannot reach it →
#     fail FAST with an actionable hint instead of the SDK-default 300s hang.
# ---------------------------------------------------------------------------

# Bounded transport timeouts (seconds). 30s connect surfaces an unreachable
# (policy-locked) endpoint quickly instead of the azure-core default 300s.
DATAPLANE_CLIENT_TIMEOUTS = {"connection_timeout": 30, "read_timeout": 120}

# Backoff schedule covering data-plane RBAC propagation.
_AUTH_RETRY_DELAYS = (0, 15, 30, 45, 60)


def _is_auth_propagation_error(exc: Exception) -> bool:
    """True for the transient 401/403 seen while a data-plane role assignment is
    still propagating (assignment exists in ARM but is not yet effective)."""
    status = getattr(exc, "status_code", None)
    code = getattr(getattr(exc, "error", None), "code", "") or ""
    text = str(exc)
    return (
        status in (401, 403)
        or code in {"AuthorizationFailure", "Forbidden"}
        or "AuthorizationFailure" in text
        or "not authorized" in text.lower()
    )


def _is_conflicting_update_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    return status in (409, None) and "conflicting update" in text


def _network_unreachable_hint(endpoint: str, exc: Exception) -> str:
    return (
        f"Could not reach '{endpoint}' from this machine ({type(exc).__name__}). "
        "This postprovision step runs from YOUR machine, and the data-plane endpoint "
        "is unreachable over the network. In a governed subscription an Azure Policy "
        "often disables public network access on AI Search / Storage, so a laptop "
        "cannot connect. Fixes: (1) run `azd up` from Azure Cloud Shell or an Azure VM "
        "in the same tenant (trusted-Azure-service access reaches the endpoint), or "
        "(2) enable public network access on the Search + Storage resources (or add "
        "your client IP to their firewall), then re-run `azd provision`. See the "
        "README section 'Deploying into a governed / network-restricted subscription'."
    )


def _run_with_auth_retry(fn, *, what: str, endpoint: str):
    """Run ``fn()`` with retry limited to the RBAC-propagation 401/403 window.

    Network-unreachable errors (connect timeout / DNS / refused) fail fast with an
    actionable hint — retrying a policy-locked endpoint only wastes minutes.
    """
    from azure.core.exceptions import ServiceRequestError

    last: Exception | None = None
    for attempt, delay in enumerate(_AUTH_RETRY_DELAYS, start=1):
        if delay:
            reason = (
                "had a conflicting resource update"
                if last is not None and _is_conflicting_update_error(last)
                else "not yet authorized"
            )
            _log(
                f"{what} {reason}; waiting {delay}s for data-plane RBAC/update state to "
                f"propagate (attempt {attempt}/{len(_AUTH_RETRY_DELAYS)})..."
            )
            time.sleep(delay)
        try:
            return fn()
        except ServiceRequestError as exc:  # transport: connect/read timeout, DNS, refused
            raise RuntimeError(_network_unreachable_hint(endpoint, exc)) from exc
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _is_auth_propagation_error(exc) or _is_conflicting_update_error(exc):
                continue
            raise
    raise RuntimeError(
        f"{what} still unauthorized after {len(_AUTH_RETRY_DELAYS)} attempts waiting for "
        f"data-plane RBAC to propagate. Confirm the deployer has the required Search "
        f"data-plane roles ('Search Index Data Contributor' + 'Search Service "
        f"Contributor') on {endpoint}, then re-run `azd provision`. Last error: {last}"
    ) from last


# ---------------------------------------------------------------------------
# STEP 2 — AI Search index
# ---------------------------------------------------------------------------
KB_INDEXER_POLL_INTERVAL_SECONDS = 5
KB_INDEXER_TIMEOUT_SECONDS = 600
KB_CHUNK_MAX_CHARS = 1200
KB_CHUNK_OVERLAP_CHARS = 100
KB_INDEXER_INTERVAL_MINUTES = 5


def _metadata_value(value: str) -> str:
    """Return an Azure Blob metadata-safe single-line ASCII-ish value."""
    return " ".join((value or "").replace("\r", "\n").split())


def kb_blob_metadata_from_markdown(
    *,
    doc_id: str,
    source: str,
    markdown: str,
    authored_source: str | None = None,
) -> dict[str, str]:
    """Metadata the native Blob indexer needs to project parent fields to chunks."""
    doc = parse_markdown(doc_id, source, markdown)
    metadata = {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "source": authored_source or doc.source,
        "assignment_group": doc.assignment_group,
        "keywords": ", ".join(doc.keywords),
        "resolution_steps": doc.resolution_steps,
    }
    return {key: _metadata_value(value) for key, value in metadata.items() if value}


def kb_blob_metadata_from_file(path: Path) -> dict[str, str]:
    return kb_blob_metadata_from_markdown(
        doc_id=path.stem,
        source=path.name,
        markdown=path.read_text(encoding="utf-8"),
    )


def load_kb_from_blob(*, blob_endpoint: str, container: str) -> list[KbDoc]:
    """Load ``*.md`` KB articles from an Azure Blob Storage container."""
    from azure.storage.blob import BlobServiceClient

    from ..shared import get_credential

    service = BlobServiceClient(
        account_url=blob_endpoint,
        credential=get_credential(),
        **DATAPLANE_CLIENT_TIMEOUTS,
    )
    container_client = service.get_container_client(container)

    docs: list[KbDoc] = []
    for blob in container_client.list_blobs():
        blob_name = getattr(blob, "name", str(blob))
        if not blob_name.lower().endswith(".md"):
            continue

        file_name = blob_name.rsplit("/", 1)[-1]
        doc_id = file_name[: -len(".md")]
        downloader = container_client.download_blob(blob_name)
        markdown = downloader.readall().decode("utf-8")
        doc = parse_markdown(doc_id, blob_name, markdown)
        properties = getattr(downloader, "properties", None)
        metadata = getattr(properties, "metadata", None) or {}
        if metadata:
            doc.title = metadata.get("title") or doc.title
            doc.source = metadata.get("source") or doc.source
            doc.assignment_group = metadata.get("assignment_group") or doc.assignment_group
        docs.append(doc)
    return docs


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
        LexicalAnalyzerName,
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
        SearchField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            searchable=True,
            filterable=True,
            analyzer_name=LexicalAnalyzerName.KEYWORD,
        ),
        SimpleField(name="parent_id", type=SearchFieldDataType.String, filterable=True),
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


def _analyzer_value(value) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _index_key_uses_keyword_analyzer(index) -> bool:
    key_field = next((field for field in getattr(index, "fields", []) if field.name == "id"), None)
    return bool(
        key_field
        and getattr(key_field, "key", False)
        and _analyzer_value(getattr(key_field, "analyzer_name", None)) == "keyword"
    )


def _index_has_projection_parent_field(index) -> bool:
    parent_field = next(
        (field for field in getattr(index, "fields", []) if field.name == "parent_id"),
        None,
    )
    return bool(parent_field and getattr(parent_field, "filterable", False))


def _create_or_replace_index(index_client, index, *, search_endpoint: str) -> None:
    """Recreate the index so Blob projection reruns leave no stale parent rows."""
    from azure.core.exceptions import ResourceNotFoundError

    existing = None
    try:
        existing = _run_with_auth_retry(
            lambda: index_client.get_index(index.name),
            what="AI Search index read",
            endpoint=search_endpoint,
        )
    except ResourceNotFoundError:
        existing = None

    if existing is not None:
        reason = (
            "lacks native projection schema"
            if (
                not _index_key_uses_keyword_analyzer(existing)
                or not _index_has_projection_parent_field(existing)
            )
            else "is being rebuilt from Blob"
        )
        _log(
            f"index '{index.name}' {reason}; deleting and recreating with "
            "keyword key analyzer + parent_id"
        )
        _delete_kb_objects_before_index_recreate(
            index_client,
            index_name=index.name,
            search_endpoint=search_endpoint,
        )
        _run_with_auth_retry(
            lambda: index_client.delete_index(index.name),
            what="AI Search index delete/recreate",
            endpoint=search_endpoint,
        )

    _run_with_auth_retry(
        lambda: index_client.create_or_update_index(index),
        what="AI Search index create/update",
        endpoint=search_endpoint,
    )


def _delete_kb_objects_before_index_recreate(
    index_client,
    *,
    index_name: str,
    search_endpoint: str,
) -> None:
    """Drop Foundry IQ Search KB objects that block deleting the referenced index."""
    from azure.core.exceptions import ResourceNotFoundError

    from .definitions.triage_agent import KB_KNOWLEDGE_BASE_NAME, KB_KNOWLEDGE_SOURCE_NAME

    if index_name != KB_KNOWLEDGE_BASE_NAME:
        return

    for kind, name, delete_fn in [
        (
            "knowledge base",
            KB_KNOWLEDGE_BASE_NAME,
            getattr(index_client, "delete_knowledge_base", None),
        ),
        (
            "knowledge source",
            KB_KNOWLEDGE_SOURCE_NAME,
            getattr(index_client, "delete_knowledge_source", None),
        ),
    ]:
        if delete_fn is None:
            continue
        try:
            _run_with_auth_retry(
                lambda name=name, delete_fn=delete_fn: delete_fn(name),
                what=f"AI Search {kind} delete before index recreate",
                endpoint=search_endpoint,
            )
            _log(f"deleted {kind} '{name}' before recreating index '{index_name}'")
        except ResourceNotFoundError:
            pass


def kb_indexing_resource_names(index_name: str) -> dict[str, str]:
    safe = index_name.replace("_", "-").lower()
    return {
        "data_source": f"{safe}-blob-ds",
        "skillset": f"{safe}-blob-skillset",
        "indexer": f"{safe}-blob-indexer",
    }


def _build_blob_data_source(
    *,
    name: str,
    storage_resource_id: str,
    container: str,
):
    from azure.search.documents.indexes.models import (
        HighWaterMarkChangeDetectionPolicy,
        NativeBlobSoftDeleteDeletionDetectionPolicy,
        SearchIndexerDataContainer,
        SearchIndexerDataSourceConnection,
        SearchIndexerDataSourceType,
    )

    return SearchIndexerDataSourceConnection(
        name=name,
        type=SearchIndexerDataSourceType.AZURE_BLOB,
        connection_string=f"ResourceId={storage_resource_id};",
        container=SearchIndexerDataContainer(name=container),
        data_change_detection_policy=HighWaterMarkChangeDetectionPolicy(
            high_water_mark_column_name="metadata_storage_last_modified"
        ),
        data_deletion_detection_policy=NativeBlobSoftDeleteDeletionDetectionPolicy(),
        description="IT Helpdesk KB markdown from Blob Storage using Search managed identity.",
    )


def _build_kb_skillset(
    *,
    name: str,
    index_name: str,
    openai_endpoint: str,
    embedding_deployment: str,
):
    from azure.search.documents.indexes.models import (
        AzureOpenAIEmbeddingSkill,
        IndexProjectionMode,
        InputFieldMappingEntry,
        OutputFieldMappingEntry,
        SearchIndexerIndexProjection,
        SearchIndexerIndexProjectionSelector,
        SearchIndexerIndexProjectionsParameters,
        SearchIndexerSkillset,
        SplitSkill,
        SplitSkillUnit,
        TextSplitMode,
    )

    split = SplitSkill(
        name="kb-split",
        description="Split KB markdown into chunks near the previous app-side section chunks.",
        context="/document",
        text_split_mode=TextSplitMode.PAGES,
        maximum_page_length=KB_CHUNK_MAX_CHARS,
        page_overlap_length=KB_CHUNK_OVERLAP_CHARS,
        unit=SplitSkillUnit.CHARACTERS,
        inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
        outputs=[OutputFieldMappingEntry(name="textItems", target_name="pages")],
    )
    embed = AzureOpenAIEmbeddingSkill(
        name="kb-embed",
        description="Embed each KB chunk with the same reduced dimension used by the index.",
        context="/document/pages/*",
        resource_url=openai_endpoint,
        deployment_name=embedding_deployment,
        model_name="text-embedding-3-large",
        dimensions=EMBEDDING_DIMENSIONS,
        auth_identity=None,
        inputs=[InputFieldMappingEntry(name="text", source="/document/pages/*")],
        outputs=[OutputFieldMappingEntry(name="embedding", target_name="content_vector")],
    )
    projection = SearchIndexerIndexProjection(
        selectors=[
            SearchIndexerIndexProjectionSelector(
                target_index_name=index_name,
                parent_key_field_name="parent_id",
                source_context="/document/pages/*",
                mappings=[
                    InputFieldMappingEntry(name="doc_id", source="/document/doc_id"),
                    InputFieldMappingEntry(name="content", source="/document/pages/*"),
                    InputFieldMappingEntry(
                        name="content_vector", source="/document/pages/*/content_vector"
                    ),
                    InputFieldMappingEntry(name="title", source="/document/title"),
                    InputFieldMappingEntry(name="source", source="/document/source"),
                    InputFieldMappingEntry(
                        name="assignment_group",
                        source="/document/assignment_group",
                    ),
                    InputFieldMappingEntry(
                        name="resolution_steps",
                        source="/document/resolution_steps",
                    ),
                ],
            )
        ],
        parameters=SearchIndexerIndexProjectionsParameters(
            projection_mode=IndexProjectionMode.SKIP_INDEXING_PARENT_DOCUMENTS
        ),
    )
    return SearchIndexerSkillset(
        name=name,
        description="Native Blob pull-indexing skillset for IT Helpdesk KB markdown.",
        skills=[split, embed],
        index_projection=projection,
    )


def _build_kb_indexer(
    *,
    name: str,
    data_source_name: str,
    skillset_name: str,
    index_name: str,
):
    from azure.search.documents.indexes.models import (
        IndexingParameters,
        IndexingParametersConfiguration,
        IndexingSchedule,
        SearchIndexer,
    )

    return SearchIndexer(
        name=name,
        data_source_name=data_source_name,
        target_index_name=index_name,
        skillset_name=skillset_name,
        description="Pull KB markdown from Blob, split, embed, and project chunks.",
        parameters=IndexingParameters(
            max_failed_items=0,
            max_failed_items_per_batch=0,
            configuration=IndexingParametersConfiguration(
                parsing_mode="text",
                indexed_file_name_extensions=".md",
                data_to_extract="contentAndMetadata",
                query_timeout=None,
            ),
        ),
        schedule=IndexingSchedule(interval=timedelta(minutes=KB_INDEXER_INTERVAL_MINUTES)),
    )


def create_kb_indexing_pipeline(
    *,
    search_endpoint: str,
    index_name: str,
    storage_resource_id: str,
    openai_endpoint: str,
    embedding_deployment: str,
    kb_container: str = "kbdocs",
) -> dict[str, str]:
    """Create/update the Blob datasource, Split+Embedding skillset, and indexer."""
    from azure.search.documents.indexes import SearchIndexerClient

    from ..shared import get_credential

    if not storage_resource_id:
        raise ValueError("storage_resource_id is required for managed-identity Blob indexing.")
    if not openai_endpoint:
        raise ValueError("openai_endpoint is required for the AzureOpenAIEmbeddingSkill.")
    if not embedding_deployment:
        raise ValueError("embedding_deployment is required for KB embeddings.")

    names = kb_indexing_resource_names(index_name)
    client = SearchIndexerClient(
        endpoint=search_endpoint, credential=get_credential(), **DATAPLANE_CLIENT_TIMEOUTS
    )
    data_source = _build_blob_data_source(
        name=names["data_source"],
        storage_resource_id=storage_resource_id,
        container=kb_container,
    )
    skillset = _build_kb_skillset(
        name=names["skillset"],
        index_name=index_name,
        openai_endpoint=openai_endpoint,
        embedding_deployment=embedding_deployment,
    )
    indexer = _build_kb_indexer(
        name=names["indexer"],
        data_source_name=names["data_source"],
        skillset_name=names["skillset"],
        index_name=index_name,
    )

    _run_with_auth_retry(
        lambda: client.create_or_update_data_source_connection(data_source),
        what="AI Search KB blob data source create/update",
        endpoint=search_endpoint,
    )
    _run_with_auth_retry(
        lambda: client.create_or_update_skillset(skillset),
        what="AI Search KB skillset create/update",
        endpoint=search_endpoint,
    )
    _run_with_auth_retry(
        lambda: client.create_or_update_indexer(indexer),
        what="AI Search KB indexer create/update",
        endpoint=search_endpoint,
    )
    _log(
        "KB native indexing pipeline ready: "
        f"dataSource={names['data_source']} skillset={names['skillset']} "
        f"indexer={names['indexer']}"
    )
    return names


def _indexer_result_status(result) -> str:
    status = getattr(result, "status", None)
    return str(getattr(status, "value", status) or "").lower()


def _indexer_error_text(result) -> str:
    errors = getattr(result, "errors", None) or []
    warnings = getattr(result, "warnings", None) or []
    parts = []
    for item in [*errors, *warnings]:
        message = getattr(item, "message", None) or str(item)
        parts.append(message)
    return "; ".join(parts)


def run_indexer(
    *,
    search_endpoint: str,
    indexer_name: str,
    wait: bool = False,
    timeout_seconds: int = KB_INDEXER_TIMEOUT_SECONDS,
) -> object | None:
    """Run an AI Search indexer and optionally wait for terminal status."""
    from azure.core.exceptions import HttpResponseError
    from azure.search.documents.indexes import SearchIndexerClient

    from ..shared import get_credential

    client = SearchIndexerClient(
        endpoint=search_endpoint, credential=get_credential(), **DATAPLANE_CLIENT_TIMEOUTS
    )
    try:
        _run_with_auth_retry(
            lambda: client.run_indexer(indexer_name),
            what=f"AI Search indexer '{indexer_name}' run",
            endpoint=search_endpoint,
        )
    except HttpResponseError as exc:
        if "already running" not in str(exc).lower():
            raise
        _log(f"indexer '{indexer_name}' is already running; polling status")

    if not wait:
        return None

    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        status = _run_with_auth_retry(
            lambda: client.get_indexer_status(indexer_name),
            what=f"AI Search indexer '{indexer_name}' status",
            endpoint=search_endpoint,
        )
        last_status = status
        result = getattr(status, "last_result", None) or getattr(status, "lastResult", None)
        result_status = _indexer_result_status(result)
        if result_status in {"success", "transientfailure"}:
            if result_status == "success":
                return status
            raise RuntimeError(
                f"AI Search indexer '{indexer_name}' failed: "
                f"{_indexer_error_text(result) or result!r}"
            )
        time.sleep(KB_INDEXER_POLL_INTERVAL_SECONDS)
    raise TimeoutError(
        f"Timed out waiting {timeout_seconds}s for AI Search indexer '{indexer_name}'. "
        f"Last status: {last_status!r}"
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
    blob_endpoint: str | None = None,
    kb_container: str | None = None,
    storage_resource_id: str | None = None,
    run: bool = True,
    wait: bool = True,
) -> None:
    """Create/refresh the KB index and native Blob pull-indexing pipeline."""
    from azure.search.documents.indexes import SearchIndexClient

    from ..shared import get_credential

    credential = get_credential()

    index_client = SearchIndexClient(
        endpoint=search_endpoint, credential=credential, **DATAPLANE_CLIENT_TIMEOUTS
    )
    index = _build_index_definition(
        index_name,
        openai_endpoint=openai_endpoint,
        embedding_deployment=embedding_deployment,
    )
    _create_or_replace_index(index_client, index, search_endpoint=search_endpoint)
    _log(f"index '{index_name}' created/updated on {search_endpoint}")

    container = kb_container or "kbdocs"
    if not blob_endpoint:
        raise RuntimeError(
            "AZURE_STORAGE_BLOB_ENDPOINT is required; Blob Storage is the only KB source."
        )
    names = create_kb_indexing_pipeline(
        search_endpoint=search_endpoint,
        index_name=index_name,
        storage_resource_id=storage_resource_id or "",
        openai_endpoint=openai_endpoint or "",
        embedding_deployment=embedding_deployment,
        kb_container=container,
    )
    if run:
        run_indexer(
            search_endpoint=search_endpoint,
            indexer_name=names["indexer"],
            wait=wait,
        )
        _log(f"indexer '{names['indexer']}' run complete")


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
    kb_connection_id: str,
    triage_chat_deployment: str | None = None,
) -> dict[str, str]:
    """Create/refresh the triage + incident Prompt Agents and persist IDs.

    Uses the **new** Azure AI Foundry Agent experience: agents are versioned
    *Prompt Agents* created through ``AIProjectClient.agents.create_version`` on
    the ``{endpoint}/api/projects/...`` resource (data-plane v1). This is the path
    that surfaces the agents in the new Foundry portal, unlike the legacy
    ``azure.ai.agents.AgentsClient`` assistants API (``asst_``-prefixed IDs) it
    replaces.

    The triage agent runs on ``triage_chat_deployment`` when supplied (a
    typically smaller/cheaper model, e.g. ``gpt-5.4-mini``), falling back to the
    main ``chat_deployment`` when unset. The incident agent always stays on the
    main ``chat_deployment`` (matching the hosted orchestrator container).

    The canonical identifier of a new-experience agent is its **name** (equal to
    ``AgentDetails.id``); that stable name is what we persist and what the runtime
    references. ``create_version`` is idempotent-friendly: re-running publishes a
    new version of the same named agent rather than duplicating it.

    Triage grounds on the **Foundry IQ knowledge base** (an Azure AI Search
    agentic-retrieval knowledge base built here from the KB index) via an MCP
    RemoteTool connection referenced by name (``kb_connection_id``) — the same
    pattern the incident agent uses for the ServiceNow APIM MCP server, NOT an
    inline Azure AI Search tool or a managed project Index.

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
        ensure_kb_knowledge_base,
        kb_mcp_url,
    )

    if not INCIDENT_INSTRUCTIONS:
        raise RuntimeError("Incident Prompt Agent instructions must not be empty.")

    # Triage gets its own deployment when provided; otherwise the main one.
    triage_deployment = triage_chat_deployment or chat_deployment

    # Data-plane: ensure the Foundry IQ knowledge base (Azure AI Search
    # agentic-retrieval knowledge source + knowledge base) exists over the KB
    # index. Returns the knowledge base name the MCP endpoint is addressed by.
    kb_name = ensure_kb_knowledge_base(
        search_endpoint=search_endpoint,
        index_name=search_index_name,
    )
    triage_kb_mcp_url = kb_mcp_url(search_endpoint, knowledge_base_name=kb_name)
    _log(f"Foundry IQ knowledge base ready -> {kb_name} ({triage_kb_mcp_url})")

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

        definitions = {
            "it-helpdesk-triage": build_triage_definition(
                chat_deployment=triage_deployment,
                kb_mcp_url=triage_kb_mcp_url,
                kb_connection_name=kb_connection_id,
            ),
            "it-helpdesk-incident": build_incident_definition(
                chat_deployment=chat_deployment,
                apim_mcp_url=apim_mcp_url,
                mcp_connection_id=mcp_connection_id,
            ),
        }
        _log(
            f"triage model={triage_deployment} | incident model={chat_deployment}"
        )

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
    triage_chat_deployment: str | None = None,
    routing_chat_deployment: str | None = None,
    reasoning_effort: str | None = None,
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
    of the same named agent. Foundry **reserves** and auto-injects the platform
    environment variables — all ``FOUNDRY_*`` and ``AGENT_*`` (including
    ``FOUNDRY_PROJECT_ENDPOINT``) **and** ``APPLICATIONINSIGHTS_CONNECTION_STRING``
    (from the project's default AppInsights connection) — so we must NOT set them
    here; the registration API rejects reserved keys with "reserved for platform
    use". We only pass the non-reserved vars the container needs: the model
    deployment, the triage sub-agent's own model deployment (which may differ,
    e.g. gpt-5.4-mini — the orchestrator must invoke triage with its model), the
    sub-agent names, and the OTEL/GenAI tracing knobs (cloud role name +
    content-recording toggle) that ``main.py``'s telemetry setup reads.
    ``main.py`` resolves the App Insights connection string from the injected env
    var (or the project's AppInsights connection as a runtime fallback).

    ``reasoning_effort`` (default ``low``) pins the orchestrator's gpt-5.x
    ``reasoning.effort`` for both its per-turn passes — the #1 latency lever. It
    is injected as ``ORCHESTRATOR_REASONING_EFFORT`` so it can be retuned via env
    (``azd env set`` + re-register) without rebuilding the container image.
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
    # NOTE: FOUNDRY_*, AGENT_*, and APPLICATIONINSIGHTS_CONNECTION_STRING are
    # reserved for platform use and injected by Foundry at run time — passing any
    # of them here fails registration with "invalid_payload ... reserved for
    # platform use". main.py reads the platform-injected FOUNDRY_PROJECT_ENDPOINT
    # and APPLICATIONINSIGHTS_CONNECTION_STRING at run time. We set only the
    # non-reserved telemetry knobs: the App Insights cloud role name
    # (OTEL_SERVICE_NAME) and the GenAI content-recording toggle, so the running
    # orchestrator tags and records its traces correctly.
    environment_variables = {
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": chat_deployment,
        # The triage sub-agent may be published on its own (smaller/cheaper)
        # deployment (e.g. gpt-5.4-mini). main.py MUST invoke the triage
        # agent_reference with THIS model — Foundry rejects a mismatch between the
        # passed model and the referenced agent's own model with 400
        # invalid_payload. Falls back to the main deployment when triage shares it.
        "TRIAGE_MODEL_DEPLOYMENT_NAME": triage_chat_deployment or chat_deployment,
        # The routing pass (intent classification + tool pick) is lightweight and
        # runs on a plain model deployment (not an agent_reference), so it is not
        # bound to the main chat deployment. Default it to the smaller triage
        # deployment (e.g. gpt-5.4-mini) when available — this cuts the dominant
        # per-turn routing latency roughly in half while keeping routing correct.
        # The incident sub-agent still runs on the main chat_deployment. Override
        # via `azd env set ROUTING_MODEL_DEPLOYMENT_NAME=...` without a rebuild.
        "ROUTING_MODEL_DEPLOYMENT_NAME": (
            routing_chat_deployment or triage_chat_deployment or chat_deployment
        ),
        "TRIAGE_AGENT_NAME": _AGENT_NAMES[0],
        "INCIDENT_AGENT_NAME": _AGENT_NAMES[1],
        # Orchestrator's own gpt-5.x reasoning effort (both per-turn passes). LOW
        # by default to cut the dominant per-turn "thinking" latency while keeping
        # routing correct. Non-reserved key -> retunable via env without a rebuild.
        "ORCHESTRATOR_REASONING_EFFORT": reasoning_effort or "low",
        "OTEL_SERVICE_NAME": _ORCHESTRATOR_NAME,
        "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED": "true",
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
