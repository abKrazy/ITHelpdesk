"""Embedding dimension invariants for live indexing and query-time search."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from helpdesk.agents import embeddings, setup


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.0] * kwargs["dimensions"])])


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddings()


def test_embed_texts_requests_configured_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeOpenAIClient()
    monkeypatch.setattr(embeddings, "_openai_client", lambda: client)

    vectors = embeddings.embed_texts(
        ["hello"], "text-embedding-3-large", dimensions=embeddings.EMBEDDING_DIMENSIONS
    )

    assert len(vectors[0]) == embeddings.EMBEDDING_DIMENSIONS
    assert client.embeddings.calls == [
        {
            "model": "text-embedding-3-large",
            "input": ["hello"],
            "dimensions": embeddings.EMBEDDING_DIMENSIONS,
        }
    ]


def test_search_index_uses_same_dimension_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_search_index_models(monkeypatch)

    index = setup._build_index_definition("it-helpdesk-kb")
    vector_field = next(field for field in index.fields if field.name == "content_vector")
    resolution_field = next(field for field in index.fields if field.name == "resolution_steps")

    assert setup.EMBEDDING_DIMENSIONS is embeddings.EMBEDDING_DIMENSIONS
    assert vector_field.vector_search_dimensions == embeddings.EMBEDDING_DIMENSIONS
    assert resolution_field.type == "Edm.String"


def test_upload_result_failures_are_loud() -> None:
    with pytest.raises(RuntimeError, match="doc-1.*dimension mismatch"):
        setup._verify_upload_results(
            [SimpleNamespace(key="doc-1", succeeded=False, error_message="dimension mismatch")]
        )


def _install_fake_search_index_models(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "azure",
        "azure.search",
        "azure.search.documents",
        "azure.search.documents.indexes",
    ]:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    models = types.ModuleType("azure.search.documents.indexes.models")

    class SearchFieldDataType:
        String = "Edm.String"
        Single = "Edm.Single"

        @staticmethod
        def Collection(inner):
            return f"Collection({inner})"

    class _Model:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class SearchIndex(_Model):
        pass

    models.SearchFieldDataType = SearchFieldDataType
    for class_name in [
        "AzureOpenAIVectorizer",
        "AzureOpenAIVectorizerParameters",
        "HnswAlgorithmConfiguration",
        "SearchableField",
        "SearchField",
        "SemanticConfiguration",
        "SemanticField",
        "SemanticPrioritizedFields",
        "SemanticSearch",
        "SimpleField",
        "VectorSearch",
        "VectorSearchProfile",
    ]:
        setattr(models, class_name, _Model)
    models.SearchIndex = SearchIndex
    monkeypatch.setitem(sys.modules, "azure.search.documents.indexes.models", models)
