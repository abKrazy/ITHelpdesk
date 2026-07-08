"""Typed configuration loaded from environment variables.

Every endpoint/name comes from the azd Bicep outputs contract (ARCHITECTURE.md
§7). NOTHING is hard-coded. Missing values are tolerated so that local/dev/mock
runs work without a live Azure environment; components that need a specific value
validate it lazily when they actually try to reach the service.
"""

from __future__ import annotations

import os
from functools import lru_cache

try:  # python-dotenv is a declared dependency but optional at runtime.
    from dotenv import load_dotenv

    load_dotenv(override=False)
except Exception:  # pragma: no cover - dotenv is best-effort convenience.
    pass


def _truthy(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Read-only view over the environment following the azd outputs contract.

    Attributes are plain strings (possibly empty). Use :meth:`require` to fetch a
    value that must be present, raising a clear error naming the missing var.
    """

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        env = dict(os.environ if environ is None else environ)
        self._env = env

        # --- Auth / identity -------------------------------------------------
        # Bicep emits AZURE_MANAGED_IDENTITY_CLIENT_ID; DefaultAzureCredential
        # also honours AZURE_CLIENT_ID. Accept either.
        self.managed_identity_client_id = (
            env.get("AZURE_MANAGED_IDENTITY_CLIENT_ID")
            or env.get("AZURE_CLIENT_ID")
            or ""
        )

        # --- Foundry ---------------------------------------------------------
        self.ai_project_endpoint = env.get("AZURE_AI_PROJECT_ENDPOINT", "")
        self.ai_project_name = env.get("AZURE_AI_PROJECT_NAME", "")
        self.ai_foundry_name = env.get("AZURE_AI_FOUNDRY_NAME", "")

        # --- Models ----------------------------------------------------------
        self.openai_endpoint = env.get("AZURE_OPENAI_ENDPOINT", "")
        self.chat_deployment = env.get("AZURE_OPENAI_CHAT_DEPLOYMENT", "")
        self.embedding_deployment = env.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")

        # --- Storage (KB source for indexing) --------------------------------
        self.storage_account_name = env.get("AZURE_STORAGE_ACCOUNT_NAME", "")
        self.storage_blob_endpoint = env.get("AZURE_STORAGE_BLOB_ENDPOINT", "")
        self.kb_container = env.get("AZURE_STORAGE_KB_CONTAINER", "kb")

        # --- Azure AI Search -------------------------------------------------
        self.search_endpoint = env.get("AZURE_SEARCH_ENDPOINT", "")
        self.search_index_name = env.get("AZURE_SEARCH_INDEX_NAME", "it-helpdesk-kb")
        self.search_service_name = env.get("AZURE_SEARCH_SERVICE_NAME", "")

        # --- ServiceNow / APIM MCP ------------------------------------------
        self.servicenow_mcp_endpoint = env.get("SERVICENOW_MCP_ENDPOINT", "")
        self.servicenow_instance_url = env.get("SERVICENOW_INSTANCE_URL", "")

        # --- Registered Foundry agent IDs (written by postprovision) ---------
        self.orchestrator_agent_id = env.get("AZURE_AI_ORCHESTRATOR_AGENT_ID", "")
        self.triage_agent_id = env.get("AZURE_AI_TRIAGE_AGENT_ID", "")
        self.incident_agent_id = env.get("AZURE_AI_INCIDENT_AGENT_ID", "")

        # --- Telemetry -------------------------------------------------------
        self.app_insights_connection_string = env.get(
            "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
        )

    @property
    def mock_mode(self) -> bool:
        """True when the stack should run without any live Azure dependency.

        Explicitly enabled via ``HELPDESK_MOCK=1`` (used by CI / the local smoke
        test), or inferred when the Foundry endpoint is absent.
        """
        if _truthy(self._env.get("HELPDESK_MOCK")):
            return True
        if _truthy(self._env.get("HELPDESK_LIVE")):
            return False
        return not self.ai_project_endpoint

    def get(self, name: str, default: str = "") -> str:
        return self._env.get(name, default)

    def require(self, name: str) -> str:
        value = self._env.get(name)
        if not value:
            raise RuntimeError(
                f"Required configuration '{name}' is not set. It is produced by the "
                "azd Bicep outputs contract (ARCHITECTURE.md §7)."
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` snapshot of the current environment."""
    return Settings()
