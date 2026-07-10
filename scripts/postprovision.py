"""postprovision.py — post-deploy wiring worker. PLACEHOLDER / SKELETON.

Owners:
  * KB upload + AI Search index build ...... Tank (infra) + Trinity (index schema)
  * Foundry agent creation ................. Trinity

Runs after `azd provision`. azd injects the Bicep outputs as environment
variables (same names as the `output` values in infra/main.bicep). This script
is intentionally a well-commented STUB — each numbered step below is implemented
by its owner. It MUST be idempotent (safe to re-run on every `azd up`).

Run manually for local testing:
    azd env get-values > .env   # then load, or rely on azd-injected env
    python scripts/postprovision.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KB_DIR = REPO_ROOT / "assets" / "kb"

# Make the src/ import roots (helpdesk umbrella + servicenow) importable when
# running from a fresh checkout that hasn't been `pip install -e .`-ed yet.
SRC_DIR = REPO_ROOT / "src"
if SRC_DIR.is_dir() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _mock() -> bool:
    return str(os.environ.get("HELPDESK_MOCK", "")).strip().lower() in {"1", "true", "yes", "on"}


def env(name: str, required: bool = True, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        print(f"[postprovision] MISSING required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return val or ""


def upload_kb_docs() -> None:
    """STEP 1 — upload assets/kb/*.md to the KB blob container.

    Uses DefaultAzureCredential (AZURE_CLIENT_ID) against AZURE_STORAGE_BLOB_ENDPOINT,
    container AZURE_STORAGE_KB_CONTAINER. Overwrites existing blobs (idempotent).
    """
    docs = sorted(KB_DIR.glob("*.md"))
    if _mock():
        print(f"[postprovision] MOCK: would upload {len(docs)} KB docs "
              f"({[d.name for d in docs]})")
        return

    blob_endpoint = env("AZURE_STORAGE_BLOB_ENDPOINT")
    container = env("AZURE_STORAGE_KB_CONTAINER", required=False, default="kbdocs")

    from azure.storage.blob import BlobServiceClient

    from helpdesk.shared import get_credential

    service = BlobServiceClient(account_url=blob_endpoint, credential=get_credential())
    try:
        service.create_container(container)
    except Exception:
        pass  # already exists — idempotent
    container_client = service.get_container_client(container)
    try:
        for doc in docs:
            container_client.upload_blob(name=doc.name, data=doc.read_bytes(), overwrite=True)
        print(f"[postprovision] uploaded {len(docs)} KB docs to {blob_endpoint}{container}")
    except Exception as exc:  # noqa: BLE001 — archival copy is non-critical
        # The archival blob copy is NOT on the RAG path: build_search_index()
        # reads assets/kb locally and pushes chunks straight to AI Search, whose
        # endpoint stays publicly reachable. Some governed subscriptions enforce
        # an Azure Policy that forces storage publicNetworkAccess=Disabled, which
        # blocks laptop-based blob uploads. Warn and continue so `azd up` still
        # completes and the triage agent stays fully grounded.
        print(
            f"[postprovision] WARNING: KB blob upload skipped ({type(exc).__name__}: {exc}). "
            "This is archival-only and does NOT affect AI Search grounding "
            "(see build_search_index). Common cause: an Azure Policy disabling "
            "storage public network access. Continuing.",
            file=sys.stderr,
        )


def build_search_index() -> None:
    """STEP 2 — (re)build the AI Search index over the KB. Idempotent."""
    if _mock():
        print("[postprovision] MOCK: would build AI Search index over the KB")
        return

    from helpdesk.agents.setup import build_search_index as _build

    _build(
        search_endpoint=env("AZURE_SEARCH_ENDPOINT"),
        index_name=env("AZURE_SEARCH_INDEX_NAME", required=False, default="it-helpdesk-kb"),
        embedding_deployment=env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
        openai_endpoint=env("AZURE_OPENAI_ENDPOINT", required=False, default=None),
    )




def create_foundry_agents() -> None:
    """STEP 3 — create/refresh the triage + incident Prompt Agents."""
    if _mock():
        print("[postprovision] MOCK: would create triage/incident Prompt Agents")
        return

    from helpdesk.agents.setup import create_foundry_agents as _create

    _create(
        project_endpoint=env("AZURE_AI_PROJECT_ENDPOINT"),
        chat_deployment=env("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        # Triage runs on its own (smaller/cheaper) deployment when provisioned;
        # falls back to the main chat deployment inside setup when unset.
        triage_chat_deployment=env(
            "AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT", required=False, default=None
        )
        or None,
        search_endpoint=env("AZURE_SEARCH_ENDPOINT"),
        search_index_name=env("AZURE_SEARCH_INDEX_NAME"),
        apim_mcp_url=env("APIM_MCP_URL"),
        # Reference the MCP connections by NAME (not full ARM id) so the portal
        # links each tool to its connection and shows it in the Tools/Connections
        # tab. The incident agent uses the ServiceNow APIM MCP connection; triage
        # grounds on the Foundry IQ knowledge base via its RemoteTool MCP connection.
        mcp_connection_id=env("AZURE_AI_MCP_CONNECTION_NAME"),
        kb_connection_id=env("AZURE_AI_KB_CONNECTION_NAME"),
    )


def create_hosted_orchestrator() -> None:
    """STEP 4 — register the MAF orchestrator as a Foundry Hosted Agent.

    The postprovision **shell** hook (postprovision.ps1/.sh) builds + pushes the
    container image server-side with ``az acr build`` (no local Docker) and exports
    ``ORCHESTRATOR_IMAGE``. We then register it via the Foundry SDK. Idempotent.
    """
    if _mock():
        print("[postprovision] MOCK: would register the hosted orchestrator agent")
        return

    image = os.environ.get("ORCHESTRATOR_IMAGE", "").strip()
    if not image:
        print(
            "[postprovision] ORCHESTRATOR_IMAGE not set; skipping hosted orchestrator "
            "registration. The postprovision shell hook builds it via 'az acr build' "
            "— re-run 'azd provision' so the hook can build + push the image.",
            file=sys.stderr,
        )
        return

    from helpdesk.agents.setup import create_hosted_orchestrator as _create

    _create(
        project_endpoint=env("AZURE_AI_PROJECT_ENDPOINT"),
        chat_deployment=env("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        # Triage runs on its own (smaller/cheaper) deployment when provisioned;
        # the orchestrator container must invoke the triage agent_reference with
        # THAT model (Foundry requires model == the agent's model). Falls back to
        # the main chat deployment inside setup when unset.
        triage_chat_deployment=env(
            "AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT", required=False, default=None
        )
        or None,
        # Routing pass runs on the smaller/faster deployment (defaults to triage's
        # mini) to cut per-turn latency; incident stays on the main deployment.
        routing_chat_deployment=env(
            "ROUTING_MODEL_DEPLOYMENT_NAME", required=False, default=None
        )
        or env("AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT", required=False, default=None)
        or None,
        # Orchestrator's own reasoning effort (default low). Forwarded so `azd up`
        # reproduces the latency tuning idempotently; retune via `azd env set
        # ORCHESTRATOR_REASONING_EFFORT=...` without a code change.
        reasoning_effort=env(
            "ORCHESTRATOR_REASONING_EFFORT", required=False, default="low"
        )
        or "low",
        image=image,
    )


def main() -> None:
    print("[postprovision] starting")
    upload_kb_docs()
    build_search_index()
    create_foundry_agents()
    create_hosted_orchestrator()
    print("[postprovision] done")


if __name__ == "__main__":
    main()
