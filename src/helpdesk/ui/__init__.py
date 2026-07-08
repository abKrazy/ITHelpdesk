"""Customer-facing chat UI (Azure App Service).

A minimal FastAPI + Jinja2 app. It forwards user messages to the Orchestrator and
renders the reply. Auth to Foundry uses the user-assigned managed identity
(``DefaultAzureCredential`` via :func:`shared.get_credential`); in mock mode the
Orchestrator runs fully in-process with no Azure dependency.
"""

from .app import app, create_app

__all__ = ["app", "create_app"]
