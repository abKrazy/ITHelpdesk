from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import helpdesk.shared as shared
import scripts.postprovision as postprovision
from helpdesk.agents import embeddings, setup
from helpdesk.agents.kb import KbDoc


def _doc(doc_id: str, *, source: str) -> KbDoc:
    return KbDoc(
        doc_id=doc_id,
        title=f"{doc_id} title",
        source=source,
        assignment_group="Service Desk",
        keywords=[],
        content="# Title\n\n## Resolution Steps\nDo the thing.",
        sections={"resolution steps": "Do the thing."},
    )


class _FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def readall(self) -> bytes:
        return self._data


class _FakeBlobContainer:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs

    def list_blobs(self):
        return [SimpleNamespace(name=name) for name in self._blobs]

    def download_blob(self, name: str) -> _FakeDownload:
        return _FakeDownload(self._blobs[name])


class _FakeBlobServiceClient:
    instances: list["_FakeBlobServiceClient"] = []
    container_client: _FakeBlobContainer

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _FakeBlobServiceClient.instances.append(self)

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


class _FakeSearchIndexClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def create_or_update_index(self, index):
        return index


class _FakeSearchClient:
    instances: list["_FakeSearchClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.uploaded: list[dict] | None = None
        _FakeSearchClient.instances.append(self)

    def merge_or_upload_documents(self, *, documents):
        self.uploaded = documents
        return [{"key": doc["id"], "succeeded": True} for doc in documents]


def _install_fake_search_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSearchClient.instances.clear()
    for name in ["azure", "azure.search", "azure.search.documents"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    documents = sys.modules["azure.search.documents"]
    documents.SearchClient = _FakeSearchClient

    indexes = types.ModuleType("azure.search.documents.indexes")
    indexes.SearchIndexClient = _FakeSearchIndexClient
    monkeypatch.setitem(sys.modules, "azure.search.documents.indexes", indexes)


def _stub_search_index_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_search_sdk(monkeypatch)
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace())
    monkeypatch.setattr(setup, "_build_index_definition", lambda *args, **kwargs: object())
    monkeypatch.setattr(setup, "_run_with_auth_retry", lambda fn, **kwargs: fn())
    monkeypatch.setattr(
        embeddings,
        "embed_texts",
        lambda texts, *args, **kwargs: [[0.0] * setup.EMBEDDING_DIMENSIONS for _ in texts],
    )


def test_build_search_index_uses_blob_docs_without_reading_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_search_index_dependencies(monkeypatch)
    monkeypatch.setattr(
        setup,
        "load_kb_from_blob",
        lambda **kwargs: [_doc("blob-only", source="authored/blob-only.md")],
    )

    def fail_local() -> list[KbDoc]:
        raise AssertionError("local KB should not be read when blob docs exist")

    monkeypatch.setattr(setup, "load_local_kb", fail_local)

    setup.build_search_index(
        search_endpoint="https://search.example.net",
        index_name="kb",
        embedding_deployment="embed",
        blob_endpoint="https://storage.example.net",
        kb_container="kbdocs",
    )

    uploaded = _FakeSearchClient.instances[0].uploaded
    assert uploaded is not None
    assert uploaded[0]["doc_id"] == "blob-only"
    assert uploaded[0]["source"] == "authored/blob-only.md"


@pytest.mark.parametrize("blob_result", [[], RuntimeError("storage unavailable")])
def test_build_search_index_raises_when_blob_empty_or_unreadable_without_reading_local(
    monkeypatch: pytest.MonkeyPatch,
    blob_result: list[KbDoc] | Exception,
) -> None:
    _stub_search_index_dependencies(monkeypatch)

    def load_blob(**kwargs) -> list[KbDoc]:
        if isinstance(blob_result, Exception):
            raise blob_result
        return blob_result

    def load_local() -> list[KbDoc]:
        raise AssertionError("local KB should not be read when blob endpoint is configured")

    monkeypatch.setattr(setup, "load_kb_from_blob", load_blob)
    monkeypatch.setattr(setup, "load_local_kb", load_local)

    with pytest.raises(RuntimeError, match="KB blob container 'kbdocs'.*azd provision"):
        setup.build_search_index(
            search_endpoint="https://search.example.net",
            index_name="kb",
            embedding_deployment="embed",
            blob_endpoint="https://storage.example.net",
            kb_container="kbdocs",
        )

    assert _FakeSearchClient.instances == []


class _FailingBlobServiceClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def create_container(self, container: str) -> None:
        raise RuntimeError(f"cannot create {container}")


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
