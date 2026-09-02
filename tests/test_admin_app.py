from __future__ import annotations

import base64
import importlib
import json
import sys
import types
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import helpdesk.shared as shared
from helpdesk.agents import setup
from helpdesk.observability import kb_gap_store
from helpdesk.observability.knowledge_gaps import REASON_TRIAGE_UNRESOLVED, record_knowledge_gap

ui_app_module = importlib.import_module("helpdesk.ui.app")


def _principal_header() -> str:
    principal = {
        "userDetails": "Admin User",
        "claims": [
            {"typ": "preferred_username", "val": "admin@example.test"},
            {"typ": "oid", "val": "admin-object-id"},
        ],
    }
    return base64.b64encode(json.dumps(principal).encode("utf-8")).decode("ascii")


@pytest.fixture()
def auth_enabled_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ui_app_module, "_admin_auth_disabled", lambda: False)
    return ui_app_module.create_app()


@pytest.fixture()
def auth_bypassed_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ui_app_module, "_admin_auth_disabled", lambda: True)
    return ui_app_module.create_app()


def test_admin_requires_easy_auth_header(auth_enabled_app) -> None:
    resp = TestClient(auth_enabled_app).get("/admin")

    assert resp.status_code == 401
    assert "text/html" in resp.headers["content-type"]
    assert "/.auth/login/aad?post_login_redirect_uri=/admin" in resp.text


def test_admin_allows_easy_auth_header(
    auth_enabled_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ui_app_module,
        "list_gaps",
        lambda status=None, limit=50: [
            {
                "question_hash": "abc",
                "question": "How do I fix the scanner?",
                "reason": "triage_unresolved",
                "tool": "troubleshoot_from_knowledge_base",
                "occurrence_count": 1,
                "last_seen_at": "2026-09-02T20:00:00Z",
            }
        ],
    )

    resp = TestClient(auth_enabled_app).get(
        "/admin",
        headers={"X-MS-CLIENT-PRINCIPAL": _principal_header()},
    )

    assert resp.status_code == 200
    assert "Knowledge gaps" in resp.text
    assert "How do I fix the scanner?" in resp.text
    assert "/admin/kb/new?question_hash=abc" in resp.text


def test_admin_bypass_allows_local_access(
    auth_bypassed_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui_app_module, "list_gaps", lambda status=None, limit=50: [])

    resp = TestClient(auth_bypassed_app).get("/admin")

    assert resp.status_code == 200
    assert "Local admin" in resp.text


@pytest.mark.asyncio
async def test_agui_and_healthz_stay_anonymous(auth_enabled_app) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=auth_enabled_app),
        base_url="http://testserver",
    ) as client:
        health = await client.get("/healthz")
        agui = await client.post(
            "/agui",
            json={
                "threadId": "admin-auth-test",
                "runId": "admin-auth-test",
                "messages": [
                    {
                        "id": "user-1",
                        "role": "user",
                        "content": "How do I reset my forgotten password?",
                    }
                ],
            },
        )

    assert health.status_code == 200
    assert agui.status_code == 200


def test_gap_list_and_status_endpoints(
    auth_enabled_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int] | tuple[str, str | None]] = []

    def fake_list_gaps(status=None, limit=50):
        calls.append((status, limit))
        return [{"question_hash": "abc", "status": status or "new"}]

    def fake_set_status(question_hash: str, status: str, note: str | None = None):
        calls.append((status, note))
        return {"question_hash": question_hash, "status": status, "reason": note}

    monkeypatch.setattr(ui_app_module, "list_gaps", fake_list_gaps)
    monkeypatch.setattr(ui_app_module, "set_status", fake_set_status)
    headers = {"X-MS-CLIENT-PRINCIPAL": _principal_header()}
    client = TestClient(auth_enabled_app)

    list_resp = client.get("/admin/gaps?status=triaged&limit=5", headers=headers)
    status_resp = client.post(
        "/admin/gaps/abc/status",
        data={"status": "dismissed", "note": "duplicate"},
        headers=headers,
    )

    assert list_resp.status_code == 200
    assert list_resp.json() == {"gaps": [{"question_hash": "abc", "status": "triaged"}]}
    assert status_resp.status_code == 200
    assert status_resp.json() == {
        "question_hash": "abc",
        "status": "dismissed",
        "reason": "duplicate",
    }
    assert calls == [("triaged", 5), ("dismissed", "duplicate")]


def test_new_kb_form_prefills_from_gap(
    auth_enabled_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ui_app_module,
        "get_gap",
        lambda question_hash: {
            "question_hash": question_hash,
            "question": "Why is my VPN showing error 9000?",
        },
    )

    resp = TestClient(auth_enabled_app).get(
        "/admin/kb/new?question_hash=abc",
        headers={"X-MS-CLIENT-PRINCIPAL": _principal_header()},
    )

    assert resp.status_code == 200
    assert "Why is my VPN showing error 9000" in resp.text
    assert 'name="gap_hash" value="abc"' in resp.text
    assert "Network Support" in resp.text


class _FakeBlob:
    uploads: list[dict] = []
    order: list[str] = []
    store: dict[str, tuple[bytes, str]] = {}

    def __init__(self, name: str = "") -> None:
        self.name = name

    def get_blob_properties(self) -> SimpleNamespace:
        if self.name not in self.store:
            raise ResourceNotFoundError(self.name)
        return SimpleNamespace(etag=self.store[self.name][1])

    def download_blob(self):
        if self.name not in self.store:
            raise ResourceNotFoundError(self.name)
        return SimpleNamespace(readall=lambda: self.store[self.name][0])

    def upload_blob(self, data: bytes, **kwargs) -> None:
        if self.name.startswith(kb_gap_store.GAP_BLOB_PREFIX):
            if "etag" in kwargs and self.name in self.store and kwargs["etag"] != self.store[self.name][1]:
                raise ResourceModifiedError(self.name)
            current = int(self.store.get(self.name, (b"", "0"))[1])
            self.store[self.name] = (data, str(current + 1))
            return
        self.order.append("upload")
        self.uploads.append({"data": data, "kwargs": kwargs})


class ResourceNotFoundError(Exception):
    pass


class ResourceModifiedError(Exception):
    pass


class _FakeContainer:
    def get_blob_client(self, name: str) -> _FakeBlob:
        self.blob_name = name
        return _FakeBlob(name)

    def list_blobs(self, *, name_starts_with: str = "") -> list[SimpleNamespace]:
        return [
            SimpleNamespace(name=name)
            for name in _FakeBlob.store
            if name.startswith(name_starts_with)
        ]


class _FakeBlobServiceClient:
    instances: list["_FakeBlobServiceClient"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.container_client = _FakeContainer()
        self.instances.append(self)

    def get_container_client(self, container: str) -> _FakeContainer:
        self.container = container
        return self.container_client


def _install_fake_blob_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeBlob.uploads.clear()
    _FakeBlob.order.clear()
    _FakeBlob.store.clear()
    _FakeBlobServiceClient.instances.clear()

    for name in ["azure", "azure.storage"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    blob_module = types.ModuleType("azure.storage.blob")
    blob_module.BlobServiceClient = _FakeBlobServiceClient
    monkeypatch.setitem(sys.modules, "azure.storage.blob", blob_module)

    core_module = types.ModuleType("azure.core")
    core_module.MatchConditions = SimpleNamespace(IfNotModified="IfNotModified")
    monkeypatch.setitem(sys.modules, "azure.core", core_module)


def test_kb_publish_uploads_markdown_metadata_and_resolves_gap(
    auth_enabled_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_blob_sdk(monkeypatch)

    credential = SimpleNamespace()
    monkeypatch.setattr(shared, "get_credential", lambda: credential)
    monkeypatch.setattr(
        ui_app_module,
        "get_settings",
        lambda: SimpleNamespace(
            storage_blob_endpoint="https://storage.example.blob.core.windows.net",
            kb_container="kbdocs",
            search_endpoint="https://search.example.net",
            search_index_name="it-helpdesk-kb",
        ),
    )
    monkeypatch.setattr(ui_app_module, "_utc_now_iso", lambda: "2026-09-02T22:00:00Z")

    indexer_calls: list[dict] = []

    def fake_run_indexer(**kwargs) -> None:
        _FakeBlob.order.append("indexer")
        indexer_calls.append(kwargs)

    monkeypatch.setattr(setup, "run_indexer", fake_run_indexer)

    status_calls: list[tuple[str, str, str | None]] = []

    def fake_set_status(question_hash: str, status: str, note: str | None = None):
        status_calls.append((question_hash, status, note))
        return {"question_hash": question_hash, "status": status}

    monkeypatch.setattr(ui_app_module, "set_status", fake_set_status)

    resp = TestClient(auth_enabled_app).post(
        "/admin/kb",
        data={
            "gap_hash": "gapabc",
            "title": "VPN Error 9000",
            "doc_id": "vpn-error-9000",
            "source": "support-authored",
            "assignment_group": "Network Support",
            "keywords": "vpn, error 9000",
            "overview": "VPN fails with error 9000.",
            "symptoms": "- Error 9000 appears.",
            "common_causes": "- Expired profile.",
            "resolution_steps": "1. Recreate the VPN profile.",
            "when_to_create_ticket": "- If the profile cannot be recreated.",
        },
        headers={"X-MS-CLIENT-PRINCIPAL": _principal_header()},
    )

    assert resp.status_code == 200
    assert resp.json()["blob"] == "authored/vpn-error-9000.md"
    assert resp.json()["indexing"]["status"] == "triggered"
    client = _FakeBlobServiceClient.instances[0]
    assert client.kwargs["account_url"] == "https://storage.example.blob.core.windows.net"
    assert client.kwargs["credential"] is credential
    assert client.container == "kbdocs"
    assert client.container_client.blob_name == "authored/vpn-error-9000.md"

    upload = _FakeBlob.uploads[0]
    markdown = upload["data"].decode("utf-8")
    metadata = upload["kwargs"]["metadata"]
    assert "---" in markdown
    assert "# VPN Error 9000" in markdown
    assert "## Resolution Steps\n1. Recreate the VPN profile." in markdown
    assert metadata == {
        "doc_id": "vpn-error-9000",
        "title": "VPN Error 9000",
        "source": "support-authored",
        "assignment_group": "Network Support",
        "keywords": "vpn, error 9000",
        "resolution_steps": "1. Recreate the VPN profile.",
        "gap_hash": "gapabc",
        "created_at": "2026-09-02T22:00:00Z",
        "author": "admin@example.test",
    }
    assert status_calls == [
        ("gapabc", "resolved", "Published KB article authored/vpn-error-9000.md")
    ]
    assert indexer_calls == [
        {
            "search_endpoint": "https://search.example.net",
            "indexer_name": "it-helpdesk-kb-blob-indexer",
            "wait": False,
        }
    ]
    assert _FakeBlob.order == ["upload", "indexer"]


def test_kb_publish_succeeds_when_run_indexer_fails(
    auth_enabled_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_blob_sdk(monkeypatch)

    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace())
    monkeypatch.setattr(
        ui_app_module,
        "get_settings",
        lambda: SimpleNamespace(
            storage_blob_endpoint="https://storage.example.blob.core.windows.net",
            kb_container="kbdocs",
            search_endpoint="https://search.example.net",
            search_index_name="it-helpdesk-kb",
        ),
    )
    monkeypatch.setattr(ui_app_module, "_utc_now_iso", lambda: "2026-09-02T22:00:00Z")

    def failing_run_indexer(**_kwargs) -> None:
        _FakeBlob.order.append("indexer")
        raise RuntimeError("search busy")

    monkeypatch.setattr(setup, "run_indexer", failing_run_indexer)

    status_calls: list[tuple[str, str, str | None]] = []

    def fake_set_status(question_hash: str, status: str, note: str | None = None):
        status_calls.append((question_hash, status, note))
        return {"question_hash": question_hash, "status": status}

    monkeypatch.setattr(ui_app_module, "set_status", fake_set_status)

    resp = TestClient(auth_enabled_app).post(
        "/admin/kb",
        data={
            "gap_hash": "gapabc",
            "title": "VPN Error 9000",
            "doc_id": "vpn-error-9000",
            "source": "support-authored",
            "assignment_group": "Network Support",
            "keywords": "vpn, error 9000",
            "resolution_steps": "1. Recreate the VPN profile.",
        },
        headers={"X-MS-CLIENT-PRINCIPAL": _principal_header()},
    )

    assert resp.status_code == 200
    assert resp.json()["indexing"]["status"] == "queued_fallback"
    assert status_calls == [
        ("gapabc", "resolved", "Published KB article authored/vpn-error-9000.md")
    ]
    assert _FakeBlob.uploads
    assert _FakeBlob.order == ["upload", "indexer"]


def test_closed_loop_gap_to_admin_author_publish_flow(
    auth_enabled_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_blob_sdk(monkeypatch)
    monkeypatch.setenv("KB_GAP_QUEUE_ENABLED", "1")
    monkeypatch.setenv("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "1")
    monkeypatch.setattr(shared, "get_credential", lambda: SimpleNamespace())
    monkeypatch.setattr(kb_gap_store, "_utc_now_iso", lambda: "2026-09-02T22:00:00Z")
    monkeypatch.setattr(ui_app_module, "_utc_now_iso", lambda: "2026-09-02T22:01:00Z")
    monkeypatch.setattr(
        ui_app_module,
        "get_settings",
        lambda: SimpleNamespace(
            storage_blob_endpoint="https://storage.example.blob.core.windows.net",
            kb_container="kbdocs",
            search_endpoint="https://search.example.net",
            search_index_name="it-helpdesk-kb",
        ),
    )
    monkeypatch.setattr(
        kb_gap_store,
        "get_settings",
        lambda: SimpleNamespace(
            storage_blob_endpoint="https://storage.example.blob.core.windows.net",
            kb_container="kbdocs",
        ),
    )

    indexer_calls: list[dict] = []
    monkeypatch.setattr(setup, "run_indexer", lambda **kwargs: indexer_calls.append(kwargs))

    gap = record_knowledge_gap(
        "How do I fix VPN error 9000?",
        REASON_TRIAGE_UNRESOLVED,
        tool="knowledge_base_retrieve",
        environ={
            "KB_GAP_HARVEST_ENABLED": "1",
            "KB_GAP_QUEUE_ENABLED": "1",
            "AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED": "1",
        },
    )
    assert gap is not None

    client = TestClient(auth_enabled_app)
    headers = {"X-MS-CLIENT-PRINCIPAL": _principal_header()}

    gaps_resp = client.get("/admin/gaps", headers=headers)
    assert gaps_resp.status_code == 200
    [listed_gap] = gaps_resp.json()["gaps"]
    assert listed_gap["question_hash"] == gap.question_hash
    assert listed_gap["question"] == "How do I fix VPN error 9000?"

    form_resp = client.get(
        f"/admin/kb/new?question_hash={gap.question_hash}",
        headers=headers,
    )
    assert form_resp.status_code == 200
    assert f'name="gap_hash" value="{gap.question_hash}"' in form_resp.text
    assert 'value="How do I fix VPN error 9000"' in form_resp.text

    publish_resp = client.post(
        "/admin/kb",
        data={
            "gap_hash": gap.question_hash,
            "title": "VPN Error 9000",
            "doc_id": "vpn-error-9000",
            "source": "support-authored",
            "assignment_group": "Network Support",
            "keywords": "vpn, error 9000",
            "resolution_steps": "1. Recreate the VPN profile.",
        },
        headers=headers,
    )

    assert publish_resp.status_code == 200
    body = publish_resp.json()
    assert body["gap"]["status"] == "resolved"
    assert body["metadata"] == {
        "doc_id": "vpn-error-9000",
        "title": "VPN Error 9000",
        "source": "support-authored",
        "assignment_group": "Network Support",
        "keywords": "vpn, error 9000",
        "resolution_steps": "1. Recreate the VPN profile.",
        "gap_hash": gap.question_hash,
        "created_at": "2026-09-02T22:01:00Z",
        "author": "admin@example.test",
    }
    assert _FakeBlob.uploads[0]["kwargs"]["metadata"] == body["metadata"]
    assert indexer_calls == [
        {
            "search_endpoint": "https://search.example.net",
            "indexer_name": "it-helpdesk-kb-blob-indexer",
            "wait": False,
        }
    ]

    resolved_resp = client.get("/admin/gaps?status=resolved", headers=headers)
    assert resolved_resp.status_code == 200
    assert resolved_resp.json()["gaps"][0]["question_hash"] == gap.question_hash
