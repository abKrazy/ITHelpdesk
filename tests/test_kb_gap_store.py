from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import helpdesk.shared as shared
from helpdesk.observability import kb_gap_store
from helpdesk.observability.knowledge_gaps import (
    REASON_INCIDENT_CREATED,
    REASON_TRIAGE_UNRESOLVED,
    record_knowledge_gap,
)


class ResourceNotFoundError(Exception):
    pass


class ResourceModifiedError(Exception):
    pass


class _FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def readall(self) -> bytes:
        return self._data


class _FakeBlob:
    def __init__(self, store: dict[str, tuple[bytes, str]], name: str) -> None:
        self._store = store
        self.name = name

    def get_blob_properties(self) -> SimpleNamespace:
        if self.name not in self._store:
            raise ResourceNotFoundError(self.name)
        return SimpleNamespace(etag=self._store[self.name][1])

    def download_blob(self) -> _FakeDownload:
        if self.name not in self._store:
            raise ResourceNotFoundError(self.name)
        return _FakeDownload(self._store[self.name][0])

    def upload_blob(self, data: bytes, **kwargs) -> None:
        if "etag" in kwargs and self.name in self._store and kwargs["etag"] != self._store[self.name][1]:
            raise ResourceModifiedError(self.name)
        current = int(self._store.get(self.name, (b"", "0"))[1])
        self._store[self.name] = (data, str(current + 1))


class _FakeContainer:
    def __init__(self, store: dict[str, tuple[bytes, str]]) -> None:
        self._store = store

    def get_blob_client(self, name: str) -> _FakeBlob:
        return _FakeBlob(self._store, name)

    def list_blobs(self, *, name_starts_with: str = "") -> list[SimpleNamespace]:
        return [
            SimpleNamespace(name=name)
            for name in self._store
            if name.startswith(name_starts_with)
        ]


class _FakeBlobServiceClient:
    instances: list["_FakeBlobServiceClient"] = []
    store: dict[str, tuple[bytes, str]] = {}

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _FakeBlobServiceClient.instances.append(self)

    def get_container_client(self, container: str) -> _FakeContainer:
        self.container = container
        return _FakeContainer(self.store)


@pytest.fixture()
def fake_blob_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[bytes, str]]:
    _FakeBlobServiceClient.instances.clear()
    _FakeBlobServiceClient.store = {}

    for name in ["azure", "azure.storage"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    blob_module = types.ModuleType("azure.storage.blob")
    blob_module.BlobServiceClient = _FakeBlobServiceClient
    monkeypatch.setitem(sys.modules, "azure.storage.blob", blob_module)

    core_module = types.ModuleType("azure.core")
    core_module.MatchConditions = SimpleNamespace(IfNotModified="IfNotModified")
    monkeypatch.setitem(sys.modules, "azure.core", core_module)

    monkeypatch.setattr(
        kb_gap_store,
        "get_settings",
        lambda: SimpleNamespace(
            storage_blob_endpoint="https://storage.example.blob.core.windows.net",
            kb_container="kbdocs",
        ),
    )
    credential = SimpleNamespace()
    monkeypatch.setattr(shared, "get_credential", lambda: credential)
    monkeypatch.delenv("KB_GAP_QUEUE_ENABLED", raising=False)
    return _FakeBlobServiceClient.store


def test_upsert_gap_creates_and_merges_without_resetting_status(
    fake_blob_store: dict[str, tuple[bytes, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter(
        [
            "2026-09-02T20:00:00Z",
            "2026-09-02T20:01:00Z",
            "2026-09-02T20:02:00Z",
        ]
    )
    monkeypatch.setattr(kb_gap_store, "_utc_now_iso", lambda: next(times))

    kb_gap_store.upsert_gap(
        SimpleNamespace(
            question_hash="abc123",
            question=None,
            reason=REASON_TRIAGE_UNRESOLVED,
            tool="troubleshoot_from_knowledge_base",
            had_citations=False,
        )
    )
    kb_gap_store.set_status("abc123", "triaged", note="support reviewed")
    kb_gap_store.upsert_gap(
        SimpleNamespace(
            question_hash="abc123",
            question="Why is my scanner flashing cyan?",
            reason=REASON_INCIDENT_CREATED,
            tool="manage_servicenow_incident",
            had_citations=True,
        )
    )

    record = kb_gap_store.get_gap("abc123")

    assert record == {
        "question_hash": "abc123",
        "question": "Why is my scanner flashing cyan?",
        "status": "triaged",
        "reason": REASON_INCIDENT_CREATED,
        "reasons": [REASON_TRIAGE_UNRESOLVED, "support reviewed", REASON_INCIDENT_CREATED],
        "tool": "manage_servicenow_incident",
        "had_citations": True,
        "occurrence_count": 2,
        "first_seen_at": "2026-09-02T20:00:00Z",
        "last_seen_at": "2026-09-02T20:02:00Z",
    }
    assert list(fake_blob_store) == ["_system/kb-gaps/abc123.json"]
    client = _FakeBlobServiceClient.instances[0]
    assert client.container == "kbdocs"
    assert client.kwargs["credential"] is shared.get_credential()


def test_list_gaps_filters_status_and_sorts_newest_first(
    fake_blob_store: dict[str, tuple[bytes, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter(
        [
            "2026-09-02T20:00:00Z",
            "2026-09-02T20:05:00Z",
            "2026-09-02T20:10:00Z",
            "2026-09-02T20:15:00Z",
        ]
    )
    monkeypatch.setattr(kb_gap_store, "_utc_now_iso", lambda: next(times))

    for question_hash in ("old", "dismissed", "newest"):
        kb_gap_store.upsert_gap(
            {
                "question_hash": question_hash,
                "question": "",
                "reason": REASON_TRIAGE_UNRESOLVED,
                "tool": None,
                "had_citations": False,
            }
        )
    kb_gap_store.set_status("dismissed", "dismissed")

    assert [gap["question_hash"] for gap in kb_gap_store.list_gaps()] == [
        "dismissed",
        "newest",
        "old",
    ]
    assert [gap["question_hash"] for gap in kb_gap_store.list_gaps(status="new")] == [
        "newest",
        "old",
    ]
    assert [gap["question_hash"] for gap in kb_gap_store.list_gaps(limit=1)] == ["dismissed"]


def test_upsert_gap_honors_toggle_and_swallows_write_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_upsert(_gap) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(kb_gap_store, "_upsert_gap", fail_upsert)
    monkeypatch.setenv("KB_GAP_QUEUE_ENABLED", "0")
    kb_gap_store.upsert_gap({"question_hash": "off"})
    assert calls == 0

    monkeypatch.setenv("KB_GAP_QUEUE_ENABLED", "1")
    kb_gap_store.upsert_gap({"question_hash": "on"})
    assert calls == 1


def test_record_knowledge_gap_persists_gap_after_span(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[str] = []

    monkeypatch.setattr(
        kb_gap_store,
        "upsert_gap",
        lambda gap: persisted.append(gap.question_hash),
    )

    gap = record_knowledge_gap(
        "Printer has a new error code",
        REASON_TRIAGE_UNRESOLVED,
        environ={},
        tracer=None,
    )

    assert gap is not None
    assert persisted == [gap.question_hash]
