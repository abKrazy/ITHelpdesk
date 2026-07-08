"""Search-client abstraction used by the triage agent.

Two implementations:
  * :class:`LocalKbSearchClient` — keyword/overlap scoring over the local KB
    markdown. Zero external dependencies; used in mock/dev/CI mode.
  * :class:`AzureAISearchClient` — hybrid (vector + keyword) query against Azure
    AI Search (``AZURE_SEARCH_INDEX_NAME``). Used when running live.

Both return the same :class:`KbHit` shape so the triage logic is identical
regardless of backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .kb import KbDoc, load_local_kb


@dataclass
class KbHit:
    """A scored knowledge-base match."""

    doc_id: str
    title: str
    source: str
    assignment_group: str
    score: float
    content: str
    resolution_steps: str


class SearchClient(Protocol):
    def search(self, query: str, top: int = 3) -> list[KbHit]:  # pragma: no cover
        ...


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if len(t) > 2}


class LocalKbSearchClient:
    """In-memory keyword-overlap search over the parsed KB docs.

    Deterministic and dependency-free so triage can be validated offline. Scoring
    weights keyword-field matches higher than body matches, mirroring the boost a
    real hybrid search gives curated keywords.
    """

    def __init__(self, docs: list[KbDoc] | None = None) -> None:
        self._docs = docs if docs is not None else load_local_kb()

    def search(self, query: str, top: int = 3) -> list[KbHit]:
        q = _tokens(query)
        if not q:
            return []
        hits: list[KbHit] = []
        for doc in self._docs:
            kw_tokens = _tokens(" ".join(doc.keywords) + " " + doc.title)
            body_tokens = _tokens(doc.content)
            kw_overlap = len(q & kw_tokens)
            body_overlap = len(q & body_tokens)
            raw = kw_overlap * 3 + body_overlap
            if raw == 0:
                continue
            score = raw / (len(q) * 3)  # normalise roughly to 0..1
            hits.append(
                KbHit(
                    doc_id=doc.doc_id,
                    title=doc.title,
                    source=doc.source,
                    assignment_group=doc.assignment_group,
                    score=round(score, 3),
                    content=doc.content,
                    resolution_steps=doc.resolution_steps,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top]


class AzureAISearchClient:
    """Hybrid search against Azure AI Search (live mode).

    Embeds the query with ``AZURE_OPENAI_EMBEDDING_DEPLOYMENT`` and issues a
    combined vector + keyword query. Imports of Azure SDKs are deferred so this
    module stays importable in mock mode.
    """

    def __init__(self, endpoint: str, index_name: str, embedding_deployment: str) -> None:
        self._endpoint = endpoint
        self._index_name = index_name
        self._embedding_deployment = embedding_deployment
        self._client = None

    def _get_client(self):
        if self._client is None:
            from azure.search.documents import SearchClient as _AzSearchClient

            from ..shared import get_credential

            self._client = _AzSearchClient(
                endpoint=self._endpoint,
                index_name=self._index_name,
                credential=get_credential(),
            )
        return self._client

    def _embed(self, text: str) -> list[float]:
        from .embeddings import embed_texts

        return embed_texts([text], self._embedding_deployment)[0]

    def search(self, query: str, top: int = 3) -> list[KbHit]:
        from azure.search.documents.models import VectorizedQuery

        client = self._get_client()
        vector = self._embed(query)
        vquery = VectorizedQuery(vector=vector, k_nearest_neighbors=top, fields="content_vector")
        results = client.search(
            search_text=query,
            vector_queries=[vquery],
            top=top,
            select=["doc_id", "title", "source", "assignment_group", "content"],
        )
        hits: list[KbHit] = []
        for r in results:
            hits.append(
                KbHit(
                    doc_id=r.get("doc_id", ""),
                    title=r.get("title", ""),
                    source=r.get("source", ""),
                    assignment_group=r.get("assignment_group", ""),
                    score=float(r.get("@search.score", 0.0)),
                    content=r.get("content", ""),
                    resolution_steps=r.get("content", ""),
                )
            )
        return hits


def get_search_client() -> SearchClient:
    """Factory: Azure AI Search when live, local KB search in mock mode."""
    from ..shared import get_settings

    settings = get_settings()
    if settings.mock_mode or not settings.search_endpoint:
        return LocalKbSearchClient()
    return AzureAISearchClient(
        endpoint=settings.search_endpoint,
        index_name=settings.search_index_name,
        embedding_deployment=settings.embedding_deployment,
    )
