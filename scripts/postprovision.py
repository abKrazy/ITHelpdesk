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
    for doc in docs:
        container_client.upload_blob(name=doc.name, data=doc.read_bytes(), overwrite=True)
    print(f"[postprovision] uploaded {len(docs)} KB docs to {blob_endpoint}{container}")


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
    )


def create_foundry_agents() -> None:
    """STEP 3 — create/refresh the 3 Foundry agents and persist their IDs."""
    if _mock():
        print("[postprovision] MOCK: would create orchestrator/triage/incident agents")
        return

    from helpdesk.agents.setup import create_foundry_agents as _create

    _create(
        project_endpoint=env("AZURE_AI_PROJECT_ENDPOINT"),
        chat_deployment=env("AZURE_OPENAI_CHAT_DEPLOYMENT"),
    )


def main() -> None:
    print("[postprovision] starting")
    upload_kb_docs()
    build_search_index()
    create_foundry_agents()
    print("[postprovision] done")


if __name__ == "__main__":
    main()
