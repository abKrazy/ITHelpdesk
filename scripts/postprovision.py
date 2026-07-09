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
        openai_endpoint=env("AZURE_OPENAI_ENDPOINT", required=False, default=None),
    )


def _derive_apim_service_name() -> str:
    """APIM service name from the gateway/MCP URL host (apim-xxxx.azure-api.net -> apim-xxxx)."""
    from urllib.parse import urlparse

    for var in ("APIM_MCP_URL", "APIM_GATEWAY_URL"):
        url = os.environ.get(var)
        if url:
            host = urlparse(url).hostname or ""
            name = host.split(".")[0]
            if name:
                return name
    return ""


def resolve_apim_key() -> str:
    """Resolve the APIM subscription key for the Incident agent's MCP tool.

    Env override first (fast path / explicit override); otherwise fetch it at
    runtime from the ``foundry-mcp-connection`` APIM subscription via ARM
    ``listSecrets``. This fallback is required because azd does NOT inject
    ``@secure()`` Bicep outputs (like APIM_SUBSCRIPTION_KEY) into the
    postprovision hook environment, so a clean ``azd up`` has no env value.
    """
    key = os.environ.get("APIM_SUBSCRIPTION_KEY", "").strip()
    if key:
        print("[postprovision] APIM key sourced from env")
        return key

    import json
    import urllib.request

    from helpdesk.shared import get_credential

    subscription_id = env("AZURE_SUBSCRIPTION_ID")
    resource_group = env("AZURE_RESOURCE_GROUP")
    service_name = _derive_apim_service_name()
    if not service_name:
        print("[postprovision] MISSING: cannot derive APIM service name from "
              "APIM_MCP_URL/APIM_GATEWAY_URL", file=sys.stderr)
        sys.exit(1)
    sid = os.environ.get("APIM_MCP_SUBSCRIPTION_NAME", "foundry-mcp-connection")

    token = get_credential().get_token("https://management.azure.com/.default").token
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}/providers/Microsoft.ApiManagement"
        f"/service/{service_name}/subscriptions/{sid}/listSecrets"
        "?api-version=2022-08-01"
    )
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=b"",
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted ARM endpoint)
        payload = json.loads(resp.read().decode("utf-8"))
    key = payload.get("primaryKey") or payload.get("secondaryKey") or ""
    if not key:
        print("[postprovision] APIM listSecrets returned no key", file=sys.stderr)
        sys.exit(1)
    print(f"[postprovision] APIM key fetched from APIM subscription '{sid}'")
    return key


def create_foundry_agents() -> None:
    """STEP 3 — create/refresh the triage + incident Prompt Agents."""
    if _mock():
        print("[postprovision] MOCK: would create triage/incident Prompt Agents")
        return

    from helpdesk.agents.setup import create_foundry_agents as _create

    _create(
        project_endpoint=env("AZURE_AI_PROJECT_ENDPOINT"),
        chat_deployment=env("AZURE_OPENAI_CHAT_DEPLOYMENT"),
        search_endpoint=env("AZURE_SEARCH_ENDPOINT"),
        apim_mcp_url=env("APIM_MCP_URL"),
        apim_key=resolve_apim_key(),
    )


def main() -> None:
    print("[postprovision] starting")
    upload_kb_docs()
    build_search_index()
    create_foundry_agents()
    print("[postprovision] done")


if __name__ == "__main__":
    main()
