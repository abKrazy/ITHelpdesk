"""Mock-mode smoke test — drives the Orchestrator through the 3 sample prompts.

Runs with ``HELPDESK_MOCK=1`` so there is NO live Azure dependency: the triage
agent searches the local KB and the incident agent uses the in-memory ServiceNow
mock. Validates routing AND results for each of the sample prompts
(assets/Sample-Prompts.txt), covering data flows §3.2–§3.4.

Runnable two ways:
    pytest tests/test_smoke.py
    python tests/test_smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure mock mode BEFORE any settings are read, and make src/ importable when
# the package hasn't been installed with `pip install -e .`.
os.environ.setdefault("HELPDESK_MOCK", "1")
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from helpdesk.orchestrator import Orchestrator  # noqa: E402


def test_lookup_incident_status() -> None:
    """Prompt 1: 'lookup details for incident INC0000057' -> incident status."""
    resp = Orchestrator().run("lookup details for incident INC0000057")
    assert resp.route == ["incident"], resp.route
    assert resp.incident is not None
    assert resp.incident.action == "lookup"
    assert resp.incident.ok is True
    assert resp.incident.incident is not None
    assert resp.incident.incident["number"] == "INC0000057"
    # Status/state is surfaced to the user.
    assert "State:" in resp.reply


def test_create_incident_with_assignment_group() -> None:
    """Prompt 2: triage (no resolve) -> incident create + assignment group."""
    resp = Orchestrator().run("Unable to log into Epic. Create a new incident.")
    assert resp.route == ["triage", "incident"], resp.route
    assert resp.triage is not None and resp.triage.resolved is False
    assert resp.incident is not None and resp.incident.action == "create"
    # Assignment group is extracted from the matching KB doc (unable-to-login.md).
    assert resp.incident.incident is not None
    assert resp.incident.incident["assignment_group"] == "Identity and Access Management"
    assert resp.incident.incident["number"].startswith("INC")


def test_update_urgency_low() -> None:
    """Prompt 3: 'update urgency for INC0010027 to low' -> urgency=3."""
    resp = Orchestrator().run("update urgency for INC0010027 to low")
    assert resp.route == ["incident"], resp.route
    assert resp.incident is not None
    assert resp.incident.action == "update"
    assert resp.incident.ok is True
    assert resp.incident.fields_changed.get("urgency") == "3"
    assert resp.incident.incident is not None
    assert resp.incident.incident["urgency"] == "3"


def _run_all() -> int:
    prompts = [
        ("lookup details for incident INC0000057", test_lookup_incident_status),
        ("Unable to log into Epic. Create a new incident.", test_create_incident_with_assignment_group),
        ("update urgency for INC0010027 to low", test_update_urgency_low),
    ]
    orch = Orchestrator()
    failures = 0
    for prompt, check in prompts:
        resp = orch.run(prompt)
        print(f"\n>>> PROMPT: {prompt}")
        print(f"    route : {' -> '.join(resp.route)}")
        print(f"    reply : {resp.reply.replace(chr(10), ' | ')}")
        try:
            check()
            print("    PASS")
        except AssertionError as exc:  # pragma: no cover - script mode
            failures += 1
            print(f"    FAIL: {exc}")
    print(f"\n=== {len(prompts) - failures}/{len(prompts)} prompts passed ===")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
