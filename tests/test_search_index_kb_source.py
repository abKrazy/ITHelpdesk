from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import helpdesk.shared as shared
import scripts.postprovision as postprovision
from helpdesk.agents import setup


class _FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def readall(self) -> bytes:
        return self._data


class _FakeBlobContainer:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs
        self.uploads: list[dict] = []

    def list_blobs(self):
        return [SimpleNamespace(name=name) for name in self._blobs]

    def download_blob(self, name: str) -> _FakeDownload:
        return _FakeDownload(self._blobs[name])

    def upload_blob(self, **kwargs) -> None:
        self.uploads.append(kwargs)


class _FakeBlobServiceClient:
    instances: list["_FakeBlobServiceClient"] = []
    container_client: _FakeBlobContainer

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _FakeBlobServiceClient.instances.append(self)

    def create_container(self, container: str) -> None:
        self.created_container = container

    def get_container_client(self, container: str) -> _FakeBlobContainer:
        self.container = container
        return self.container_client


def _install_fake_blob_sdk(monkeypatch: pytest.MonkeyPatch, blobs: dict[str, bytes]) -> None:
    _FakeBlobServiceClient.instances.clear()
    _FakeBlobServiceClient.container_client = _FakeBlobContainer(blobs)
    for name in ["azure", "azure.storage"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    blob_module = types.ModuleType("azure.storage.blob")
    blob_module.BlobServiceClient = _FakeBlobServiceClient
    monkeypatch.setitem(sys.modules, "azure.storage.blob", blob_module)


def test_load_kb_from_blob_parses_markdown_and_ignores_non_md(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_blob_sdk(
        monkeypatch,
        {
            "authored/vpn-error-812.md": (
                b"# VPN Error 812\n\n## Recommended Assignment Group\nNetwork Support\n\n"
                b"## Keywords\nvpn, error 812\n\n## Resolution Steps\nReconnect."
            ),
            "notes/readme.txt": b"ignore me",
        },
    )
    credential = SimpleNamespace()
    monkeypatch.setattr(shared, "get_credential", lambda: credential)

    docs = setup.load_kb_from_blob(
        blob_endpoint="https://storage.example.blob.core.windows.net",
        container="kbdocs",
    )

    assert len(docs) == 1
    assert docs[0].doc_id == "vpn-error-812"
    assert docs[0].source == "authored/vpn-error-812.md"
    assert docs[0].title == "VPN Error 812"
    assert docs[0].assignment_group == "Network Support"
    assert docs[0].keywords == ["vpn", "error 812"]

    client = _FakeBlobServiceClient.instances[0]
    assert client.kwargs == {
        "account_url": "https://storage.example.blob.core.windows.net",
        "credential": credential,
        "connection_timeout": 30,
        "read_timeout": 120,
    }
    assert client.container == "kbdocs"


def test_kb_blob_metadata_from_markdown_matches_index_contract() -> None:
    metadata = setup.kb_blob_metadata_from_markdown(
        doc_id="vpn-connectivity",
        source="vpn-connectivity.md",
        markdown=(
            "# VPN Connectivity Troubleshooting\n\n"
            "## Recommended Assignment Group\nNetwork Support\n\n"
            "## Keywords\nVPN, remote access\n\n"
            "## Resolution Steps\n1. Verify internet access.\n2. Reconnect VPN.\n"
        ),
    )

    assert metadata == {
        "doc_id": "vpn-connectivity",
        "title": "VPN Connectivity Troubleshooting",
        "source": "vpn-connectivity.md",
        "assignment_group": "Network Support",
        "keywords": "VPN, remote access",
        "resolution_steps": "1. Verify internet access. 2. Reconnect VPN.",
    }


class _FakeSearchIndexClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def create_or_update_index(self, index):
        return index


class _FakeSearchIndexerClient:
    instances: list["_FakeSearchIndexerClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.data_source = None
        self.skillset = None
        self.indexer = None
        self.operations: list[str] = []
        self.runs: list[str] = []
        _FakeSearchIndexerClient.instances.append(self)

    def create_or_update_data_source_connection(self, data_source):
        self.operations.append("data_source")
        self.data_source = data_source
        return data_source

    def create_or_update_skillset(self, skillset):
        self.operations.append("skillset")
        self.skillset = skillset
        return skillset

    def create_or_update_indexer(self, indexer):
        self.operations.append("indexer")
        self.indexer = indexer
        return indexer

    def run_indexer(self, name: str) -> None:
        self.runs.append(name)

    def get_indexer_status(self, name: str):
        return SimpleNamespace(last_result=SimpleNamespace(status="success", errors=[]))


class _Model:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


def _install_fake_search_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSearchIndexerClient.instances.clear()
    for name in [
        "azure",
        "azure.core",
        "azure.core.exceptions",
        "azure.search",
        "azure.search.documents",
    ]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["azure.core.exceptions"].HttpResponseError = type(
        "HttpResponseError", (Exception,), {}
    )

    indexes = types.ModuleType("azure.search.documents.indexes")
    indexes.SearchIndexClient = _FakeSearchIndexClient
    indexes.SearchIndexerClient = _FakeSearchIndexerClient
    monkeypatch.setitem(sys.modules, "azure.search.documents.indexes", indexes)

    models = types.ModuleType("azure.search.documents.indexes.models")
    for class_name in [
        "AzureOpenAIEmbeddingSkill",
        "FieldMapping",
        "HighWaterMarkChangeDetectionPolicy",
        "IndexingParameters",
        "IndexingParametersConfiguration",
        "IndexingSchedule",
        "InputFieldMappingEntry",
        "NativeBlobSoftDeleteDeletionDetectionPolicy",
        "OutputFieldMappingEntry",
        "SearchIndexer",
        "SearchIndexerDataContainer",
        "SearchIndexerDataSourceConnection",
        "SearchIndexerIndexProjection",
        "SearchIndexerIndexProjectionSelector",
        "SearchIndexerIndexProjectionsParameters",
        "SearchIndexerSkillset",
        "SplitSkill",
    ]:
        setattr(models, class_name, _Model)
    models.SearchIndexerDataSourceType = SimpleNamespace(AZURE_BLOB="azureblob")
    models.TextSplitMode = SimpleNamespace(PAGES="pages")
    models.SplitSkillUnit = SimpleNamespace(CHARACTERS="characters")
    models.IndexProjectionMode = SimpleNamespace(
        SKIP_INDEXING_PARENT_DOCUMENTS="skipIndexingParentDocuments"
    )
    monkeypatch.setitem(sys.modules, "azure.search.documents.indexes.models", models)


def _stub_search_index_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_search_sdk(monkeypatch)
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace())
    monkeypatch.setattr(setup, "_build_index_definition", lambda *args, **kwargs: object())
    monkeypatch.setattr(setup, "_create_or_replace_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup, "_run_with_auth_retry", lambda fn, **kwargs: fn())


def test_build_search_index_creates_native_pipeline_and_runs_indexer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_search_index_dependencies(monkeypatch)

    setup.build_search_index(
        search_endpoint="https://search.example.net",
        index_name="it-helpdesk-kb",
        embedding_deployment="embed",
        openai_endpoint="https://aoai.example.net",
        blob_endpoint="https://storage.example.net",
        kb_container="kbdocs",
        storage_resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/st",
    )

    pipeline_client, run_client = _FakeSearchIndexerClient.instances
    assert pipeline_client.data_source.connection_string.endswith("/storageAccounts/st;")
    assert pipeline_client.data_source.container.name == "kbdocs"
    assert pipeline_client.skillset.skills[0].maximum_page_length == setup.KB_CHUNK_MAX_CHARS
    assert pipeline_client.skillset.skills[1].dimensions == setup.EMBEDDING_DIMENSIONS
    assert pipeline_client.skillset.index_projection.selectors[0].target_index_name == (
        "it-helpdesk-kb"
    )
    assert pipeline_client.skillset.index_projection.selectors[0].parent_key_field_name == (
        "parent_id"
    )
    mappings = {
        mapping.name: mapping.source
        for mapping in pipeline_client.skillset.index_projection.selectors[0].mappings
    }
    assert mappings["doc_id"] == "/document/doc_id"
    assert mappings["content"] == "/document/pages/*"
    assert mappings["content_vector"] == "/document/pages/*/content_vector"
    assert mappings["assignment_group"] == "/document/assignment_group"
    assert pipeline_client.indexer.schedule.interval.total_seconds() == 300
    assert run_client.runs == ["it-helpdesk-kb-blob-indexer"]


def test_create_kb_indexing_pipeline_is_idempotent_and_uses_managed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_search_index_dependencies(monkeypatch)

    names = setup.create_kb_indexing_pipeline(
        search_endpoint="https://search.example.net",
        index_name="it-helpdesk-kb",
        storage_resource_id=(
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.Storage/storageAccounts/st"
        ),
        openai_endpoint="https://aoai.example.net",
        embedding_deployment="embed",
        kb_container="kbdocs",
    )

    [client] = _FakeSearchIndexerClient.instances
    assert names == {
        "data_source": "it-helpdesk-kb-blob-ds",
        "skillset": "it-helpdesk-kb-blob-skillset",
        "indexer": "it-helpdesk-kb-blob-indexer",
    }
    assert client.operations == ["data_source", "skillset", "indexer"]
    assert client.data_source.connection_string == (
        "ResourceId=/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.Storage/storageAccounts/st;"
    )
    assert "AccountKey" not in client.data_source.connection_string
    assert "SharedAccessSignature" not in client.data_source.connection_string
    assert "sig=" not in client.data_source.connection_string.lower()
    assert client.skillset.skills[1].auth_identity is None
    assert client.indexer.data_source_name == names["data_source"]
    assert client.indexer.skillset_name == names["skillset"]


def test_create_or_replace_index_deletes_existing_without_keyword_analyzer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeIndexClient:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.deleted_kb: list[str] = []
            self.deleted_ks: list[str] = []
            self.updated: list[str] = []

        def get_index(self, name: str):
            return SimpleNamespace(
                fields=[SimpleNamespace(name="id", key=True, analyzer_name=None)]
            )

        def delete_index(self, name: str) -> None:
            self.deleted.append(name)

        def delete_knowledge_base(self, name: str) -> None:
            self.deleted_kb.append(name)

        def delete_knowledge_source(self, name: str) -> None:
            self.deleted_ks.append(name)

        def create_or_update_index(self, index):
            self.updated.append(index.name)
            return index

    for name in ["azure", "azure.core", "azure.core.exceptions"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["azure.core.exceptions"].ResourceNotFoundError = type(
        "ResourceNotFoundError", (Exception,), {}
    )
    monkeypatch.setattr(setup, "_run_with_auth_retry", lambda fn, **kwargs: fn())
    client = _FakeIndexClient()

    setup._create_or_replace_index(
        client,
        SimpleNamespace(name="it-helpdesk-kb"),
        search_endpoint="https://search.example.net",
    )

    assert client.deleted == ["it-helpdesk-kb"]
    assert client.deleted_kb == ["it-helpdesk-kb"]
    assert client.deleted_ks == ["it-helpdesk-kb-source"]
    assert client.updated == ["it-helpdesk-kb"]


def test_build_search_index_requires_blob_endpoint_and_storage_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_search_index_dependencies(monkeypatch)

    with pytest.raises(RuntimeError, match="AZURE_STORAGE_BLOB_ENDPOINT"):
        setup.build_search_index(
            search_endpoint="https://search.example.net",
            index_name="kb",
            embedding_deployment="embed",
            openai_endpoint="https://aoai.example.net",
            blob_endpoint="",
            kb_container="kbdocs",
            storage_resource_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/st",
        )

    with pytest.raises(ValueError, match="storage_resource_id"):
        setup.build_search_index(
            search_endpoint="https://search.example.net",
            index_name="kb",
            embedding_deployment="embed",
            openai_endpoint="https://aoai.example.net",
            blob_endpoint="https://storage.example.net",
            kb_container="kbdocs",
            storage_resource_id="",
        )


class _FailingBlobServiceClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def create_container(self, container: str) -> None:
        raise RuntimeError(f"cannot create {container}")


def test_upload_kb_docs_sets_indexer_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HELPDESK_MOCK", raising=False)
    monkeypatch.setenv("AZURE_STORAGE_BLOB_ENDPOINT", "https://storage.example.net/")
    monkeypatch.setenv("AZURE_STORAGE_KB_CONTAINER", "kbdocs")
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace())

    _install_fake_blob_sdk(monkeypatch, {})
    for name in ["azure.core", "azure.core.exceptions"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["azure.core.exceptions"].HttpResponseError = type(
        "HttpResponseError", (Exception,), {}
    )

    postprovision.upload_kb_docs()

    uploads = _FakeBlobServiceClient.container_client.uploads
    password = next(upload for upload in uploads if upload["name"] == "password-reset.md")
    assert password["overwrite"] is True
    assert password["metadata"]["doc_id"] == "password-reset"
    assert password["metadata"]["title"] == "Password Reset and Login Assistance"
    assert password["metadata"]["source"] == "password-reset.md"
    assert password["metadata"]["assignment_group"] == "Service Desk"
    assert "Use self-service password reset" in password["metadata"]["resolution_steps"]


def test_upload_kb_docs_is_fatal_when_blob_upload_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HELPDESK_MOCK", raising=False)
    monkeypatch.setenv("AZURE_STORAGE_BLOB_ENDPOINT", "https://storage.example.net/")
    monkeypatch.setenv("AZURE_STORAGE_KB_CONTAINER", "kbdocs")
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace())

    for name in ["azure", "azure.core", "azure.core.exceptions", "azure.storage"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    exceptions = sys.modules["azure.core.exceptions"]
    exceptions.HttpResponseError = type("HttpResponseError", (Exception,), {})

    blob_module = types.ModuleType("azure.storage.blob")
    blob_module.BlobServiceClient = _FailingBlobServiceClient
    monkeypatch.setitem(sys.modules, "azure.storage.blob", blob_module)

    with pytest.raises(RuntimeError, match="required source for AI Search indexing"):
        postprovision.upload_kb_docs()
