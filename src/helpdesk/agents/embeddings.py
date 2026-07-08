"""Embedding helper — wraps Azure OpenAI embeddings via the Foundry project.

Used by the indexing step (``setup.build_search_index``) and the live search
client. Azure imports are deferred so mock-mode never needs them.
"""

from __future__ import annotations


def _openai_client():
    """Return an Azure OpenAI client authenticated with the managed identity."""
    from azure.identity import get_bearer_token_provider
    from openai import AzureOpenAI

    from ..shared import get_credential, get_settings

    settings = get_settings()
    endpoint = settings.openai_endpoint or settings.ai_project_endpoint
    if not endpoint:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT (or AZURE_AI_PROJECT_ENDPOINT) is required to embed."
        )
    token_provider = get_bearer_token_provider(
        get_credential(), "https://cognitiveservices.azure.com/.default"
    )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version="2024-10-21",
    )


def embed_texts(texts: list[str], deployment: str) -> list[list[float]]:
    """Embed a batch of texts, returning one vector per input."""
    if not deployment:
        raise RuntimeError("AZURE_OPENAI_EMBEDDING_DEPLOYMENT is not configured.")
    client = _openai_client()
    resp = client.embeddings.create(model=deployment, input=texts)
    return [item.embedding for item in resp.data]
