"""Live-regime search score tests for triage deflection.

Azure AI Search hybrid @search.score values are RRF scores (~0.01..0.03), so
live confidence must use semantic rerankerScore when available.
"""

from __future__ import annotations

import sys
import types

import pytest

from helpdesk.agents.search_client import AzureAISearchClient, KbHit
from helpdesk.agents.triage import TriageAgent
from helpdesk.orchestrator import Orchestrator, TICKET_OFFER_MARKER


class _StaticSearch:
    def __init__(self, hits: list[KbHit]) -> None:
        self._hits = hits

    def search(self, query: str, top: int = 3) -> list[KbHit]:  # noqa: ARG002
        return self._hits[:top]


def _hit(*, score: float = 0.02, reranker_score: float | None = 2.5) -> KbHit:
    return KbHit(
        doc_id="laptop-slow",
        title="Laptop Running Slow",
        source="laptop-slow.md",
        assignment_group="Desktop Support",
        score=score,
        content="## Symptoms\nLaptop performance issues.",
        resolution_steps="1. Close unnecessary applications.\n2. Restart the laptop.",
        reranker_score=reranker_score,
    )


def test_live_reranker_confident_hit_deflects_despite_low_rrf_score() -> None:
    triage = TriageAgent(search_client=_StaticSearch([_hit(score=0.02, reranker_score=2.5)]))
    resp = Orchestrator(triage_agent=triage).run(
        "my laptop is running slow. please file a ticket."
    )

    assert resp.route == ["triage"], resp.route
    assert resp.incident is None
    assert resp.triage is not None and resp.triage.has_confident_resolution is True
    assert TICKET_OFFER_MARKER in resp.reply
    assert "Close unnecessary applications" in resp.reply


@pytest.mark.parametrize("reranker_score", [0.8, None])
def test_live_regime_weak_or_missing_reranker_does_not_deflect(
    reranker_score: float | None,
) -> None:
    triage = TriageAgent(search_client=_StaticSearch([_hit(score=0.02, reranker_score=reranker_score)]))
    result = triage.run("what is the weather today")

    assert result.has_confident_resolution is False
    assert result.resolved is False


def test_confirmation_after_reranker_based_offer_creates_ticket() -> None:
    triage = TriageAgent(search_client=_StaticSearch([_hit(score=0.02, reranker_score=2.5)]))
    orch = Orchestrator(triage_agent=triage)
    original = "my laptop is running slow. please file a ticket."
    offer = orch.run(original)
    resp = orch.run(
        "go ahead",
        history=[
            {"role": "user", "content": original},
            {"role": "assistant", "content": offer.reply},
        ],
    )

    assert offer.triage is not None and offer.triage.has_confident_resolution is True
    assert TICKET_OFFER_MARKER in offer.reply
    assert resp.route == ["triage", "incident"], resp.route
    assert resp.incident is not None and resp.incident.action == "create"
    assert resp.incident.incident is not None
    assert resp.incident.incident["short_description"] == "my laptop is running slow."
    assert resp.incident.incident["assignment_group"] == "Desktop Support"


def test_azure_search_requests_semantic_ranking_and_returns_clean_deduped_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_vector_model(monkeypatch)

    class _FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def search(self, **kwargs):
            self.calls.append(kwargs)
            return [
                {
                    "doc_id": "laptop-slow",
                    "title": "Laptop Running Slow",
                    "source": "laptop-slow.md",
                    "assignment_group": "Desktop Support",
                    "content": "## Symptoms\nSlow laptop.",
                    "resolution_steps": "1. Close unnecessary applications.",
                    "@search.score": 0.021,
                    "@search.rerankerScore": 2.7,
                },
                {
                    "doc_id": "laptop-slow",
                    "title": "Laptop Running Slow",
                    "source": "laptop-slow.md",
                    "assignment_group": "Desktop Support",
                    "content": "## Overview\nDuplicate chunk.",
                    "resolution_steps": "1. Close unnecessary applications.",
                    "@search.score": 0.019,
                    "@search.rerankerScore": 2.6,
                },
                {
                    "doc_id": "vpn",
                    "title": "VPN Connectivity",
                    "source": "vpn.md",
                    "assignment_group": "Network Support",
                    "content": "## Resolution Steps\nReconnect VPN.",
                    "@search.score": 0.018,
                    "@search.rerankerScore": None,
                },
            ]

    fake_client = _FakeClient()
    search = AzureAISearchClient("https://example.search.windows.net", "kb", "embed")
    search._client = fake_client
    monkeypatch.setattr(search, "_embed", lambda _query: [0.0, 1.0])

    hits = search.search("slow laptop", top=3)

    call = fake_client.calls[0]
    assert call["query_type"] == "semantic"
    assert call["semantic_configuration_name"] == "kb-semantic"
    assert call["top"] == 9
    assert "resolution_steps" in call["select"]
    assert len(hits) == 2
    assert hits[0].source == "laptop-slow.md"
    assert hits[0].resolution_steps == "1. Close unnecessary applications."
    assert hits[0].reranker_score == 2.7
    assert hits[1].source == "vpn.md"
    assert hits[1].resolution_steps == "## Resolution Steps\nReconnect VPN."
    assert hits[1].reranker_score is None


def _install_fake_vector_model(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["azure", "azure.search", "azure.search.documents"]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    models = types.ModuleType("azure.search.documents.models")

    class VectorizedQuery:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    models.VectorizedQuery = VectorizedQuery
    monkeypatch.setitem(sys.modules, "azure.search.documents.models", models)
