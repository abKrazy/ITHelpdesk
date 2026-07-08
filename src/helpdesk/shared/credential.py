"""Azure credential helper — DefaultAzureCredential pinned to the managed
identity client ID when one is provided (App Service / Foundry runtime), falling
back to the ambient developer credential locally.

Imports of ``azure-identity`` are deferred so mock-mode code paths (and CI
without Azure libs) never pay the import cost or fail.
"""

from __future__ import annotations

from typing import Any

from .config import get_settings


def get_credential(client_id: str | None = None) -> Any:
    """Return a ``DefaultAzureCredential``.

    When a user-assigned managed identity client ID is available (from
    ``AZURE_MANAGED_IDENTITY_CLIENT_ID`` / ``AZURE_CLIENT_ID``), it is passed via
    ``managed_identity_client_id`` so the correct identity is used in Azure.
    """
    from azure.identity import DefaultAzureCredential

    cid = client_id if client_id is not None else get_settings().managed_identity_client_id
    if cid:
        return DefaultAzureCredential(managed_identity_client_id=cid)
    return DefaultAzureCredential()


async def get_async_credential(client_id: str | None = None) -> Any:
    """Async variant used by async Azure SDK clients."""
    from azure.identity.aio import DefaultAzureCredential

    cid = client_id if client_id is not None else get_settings().managed_identity_client_id
    if cid:
        return DefaultAzureCredential(managed_identity_client_id=cid)
    return DefaultAzureCredential()
