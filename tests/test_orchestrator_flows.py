"""End-to-end Orchestrator behaviour across ALL 4 user capabilities + edge cases.

Runs entirely in mock mode (``HELPDESK_MOCK=1`` — set by ``conftest.py``): triage
searches the local KB (``assets/kb/*.md``) and the incident agent uses the
in-memory ServiceNow mock seeded with the sample incidents. No live Azure or
ServiceNow is required, so the same routing/reply contract the UI depends on is
validated offline.

Capabilities under test (ARCHITECTURE.md §3):
  * §3.1 Triage & RESOLVE from KB (no ticket created)   -> test_resolve_*
  * §3.2 Create & assign incident (escalation)          -> test_create_*
  * §3.3 Check ticket status                             -> test_lookup_*
  * §3.4 Update ticket                                   -> test_update_*

Plus failure / edge modes: unknown incident, unresolved-then-escalate handoff,
ambiguous/empty prompts, and urgency-label variants.
"""

from __future__ import annotations

import pytest

from helpdesk.agents.incident import IncidentAgent
from helpdesk.orchestrator import Orchestrator, TICKET_OFFER_MARKER


@pytest.fixture()
def orch() -> Orchestrator:
    """A fresh mock-backed Orchestrator per test (isolated ServiceNow state)."""
    return Orchestrator()


# ---------------------------------------------------------------------------
# §3.1 — Triage & RESOLVE from the KB (the 4th flow): NO ticket is created.
# ---------------------------------------------------------------------------
def test_resolve_password_reset_from_kb_without_ticket(orch: Orchestrator) -> None:
    """A KB-answerable question resolves from the KB and creates NO incident."""
    resp = orch.run("How do I reset my forgotten password?")

    # Routed to triage only — the incident agent is never engaged.
    assert resp.route == ["triage"], resp.route
    assert resp.incident is None, "no incident should be created for a KB resolve"

    # Triage confidently resolved from the knowledge base...
    assert resp.triage is not None
    assert resp.triage.resolved is True
    # ...with resolution steps AND a citation surfaced to the user.
    assert resp.triage.citations, "a KB citation must be returned"
    assert "Password Reset" in resp.triage.citations[0]
    assert "self-service password reset" in resp.reply.lower()
    # The KB resolution steps are echoed back.
    assert "resolve this" in resp.reply.lower()


def test_resolve_vpn_from_kb_without_ticket(orch: Orchestrator) -> None:
    """A second KB-answerable question (VPN) also resolves without a ticket."""
    resp = orch.run("My VPN keeps disconnecting, how do I fix it?")

    assert resp.route == ["triage"], resp.route
    assert resp.incident is None
    assert resp.triage is not None and resp.triage.resolved is True
    assert "VPN Connectivity" in resp.triage.citations[0]
    assert "reconnect vpn" in resp.reply.lower()


# ---------------------------------------------------------------------------
# §3.2 — Create & assign incident (escalation): assignment group from KB.
# ---------------------------------------------------------------------------
def test_create_incident_pulls_assignment_group_from_kb(orch: Orchestrator) -> None:
    """'Create a new incident' first deflects with KB steps when confident."""
    resp = orch.run("Unable to log into Epic. Create a new incident.")

    assert resp.route == ["triage"], resp.route
    assert resp.triage is not None and resp.triage.has_confident_resolution is True
    assert resp.incident is None
    assert "login" in resp.reply.lower()
    assert "Identity and Access Management" in resp.reply
    assert TICKET_OFFER_MARKER in resp.reply
    assert "Referenced KB" in resp.reply


def test_create_defaults_to_medium_urgency(orch: Orchestrator) -> None:
    """A newly created incident defaults to medium urgency (ServiceNow '2')."""
    offer = orch.run("Unable to log into Epic. Create a new incident.")
    resp = orch.run(
        "go ahead",
        history=[
            {"role": "user", "content": "Unable to log into Epic. Create a new incident."},
            {"role": "assistant", "content": offer.reply},
        ],
    )
    assert resp.incident is not None and resp.incident.incident is not None
    assert resp.incident.incident["urgency"] == "2"


def test_create_intent_with_confident_laptop_kb_deflects_without_incident() -> None:
    """A create request with confident KB steps offers deflection before filing."""

    class SpyIncidentAgent:
        created = False

        def create(self, *_args, **_kwargs):  # pragma: no cover - should not be called
            self.created = True
            raise AssertionError("incident create should not be called")

        def lookup(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("lookup should not be called")

        def update(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("update should not be called")

    incident = SpyIncidentAgent()
    resp = Orchestrator(incident_agent=incident).run(
        "my laptop is running slow. please file a ticket."
    )

    assert resp.route == ["triage"], resp.route
    assert resp.incident is None
    assert incident.created is False
    assert resp.triage is not None and resp.triage.has_confident_resolution is True
    assert "Close unnecessary applications" in resp.reply
    assert "Desktop Support" in resp.reply
    assert TICKET_OFFER_MARKER in resp.reply


def test_confirmation_after_offer_creates_from_original_problem(orch: Orchestrator) -> None:
    """A follow-up confirmation creates from the original problem, not 'go ahead'."""
    original = "my laptop is running slow. please file a ticket."
    offer = orch.run(original)
    resp = orch.run(
        "go ahead",
        history=[
            {"role": "user", "content": original},
            {"role": "assistant", "content": offer.reply},
        ],
    )

    assert resp.route == ["triage", "incident"], resp.route
    assert resp.incident is not None and resp.incident.action == "create"
    assert resp.incident.incident is not None
    assert resp.incident.incident["short_description"] == "my laptop is running slow."
    assert resp.incident.incident["short_description"] != "go ahead"
    assert resp.incident.incident["assignment_group"] == "Desktop Support"


def test_create_intent_with_no_kb_match_creates_immediately(orch: Orchestrator) -> None:
    """No confident KB hit means there is nothing useful to deflect with."""
    resp = orch.run("Please file a ticket for qzxv jklm nprst.")

    assert resp.route == ["triage", "incident"], resp.route
    assert resp.triage is not None and resp.triage.has_kb_match is False
    assert resp.incident is not None and resp.incident.action == "create"


# ---------------------------------------------------------------------------
# §3.3 — Check ticket status (lookup an existing incident).
# ---------------------------------------------------------------------------
def test_lookup_incident_status(orch: Orchestrator) -> None:
    resp = orch.run("lookup details for incident INC0000057")

    assert resp.route == ["incident"], resp.route
    assert resp.incident is not None
    assert resp.incident.action == "lookup"
    assert resp.incident.ok is True
    assert resp.incident.incident is not None
    assert resp.incident.incident["number"] == "INC0000057"
    assert resp.incident.incident["assignment_group"] == "End User Computing"
    # State/urgency are surfaced to the user.
    assert "State: In Progress" in resp.reply


def test_lookup_incident_status_renders_state_label(orch: Orchestrator) -> None:
    resp = orch.run("lookup details for incident INC0010027")

    assert resp.incident is not None
    assert resp.incident.incident is not None
    assert resp.incident.incident["state"] == "1"
    assert resp.incident.incident["state_label"] == "New"
    assert "State: New" in resp.reply


# ---------------------------------------------------------------------------
# §3.4 — Update ticket (change urgency to low -> '3').
# ---------------------------------------------------------------------------
def test_update_urgency_to_low(orch: Orchestrator) -> None:
    resp = orch.run("update urgency for INC0010027 to low")

    assert resp.route == ["incident"], resp.route
    assert resp.incident is not None
    assert resp.incident.action == "update"
    assert resp.incident.ok is True
    assert resp.incident.fields_changed.get("urgency") == "3"
    assert resp.incident.incident is not None
    assert resp.incident.incident["urgency"] == "3"
    assert "Low" in resp.reply


def test_proposal_mode_gates_create_without_executing(orch: Orchestrator) -> None:
    resp = orch.run("Please file a ticket for qzxv jklm nprst.", propose_writes=True)

    assert resp.route == ["triage", "incident"]
    assert resp.incident is None
    assert resp.servicenow_write_proposal is not None
    assert resp.servicenow_write_proposal["operation"] == "create"
    assert resp.servicenow_write_proposal["urgency"] == "2"


def test_proposal_mode_gates_update_without_executing(orch: Orchestrator) -> None:
    before = orch.run("lookup details for incident INC0010027").incident.incident["urgency"]
    resp = orch.run("update urgency for INC0010027 to low", propose_writes=True)
    after = orch.run("lookup details for incident INC0010027").incident.incident["urgency"]

    assert resp.route == ["incident"]
    assert resp.incident is None
    assert resp.servicenow_write_proposal == {
        "operation": "update",
        "incident_number": "INC0010027",
        "delta": {"urgency": "3"},
        "summary": "update urgency for INC0010027 to low",
    }
    assert after == before


def test_proposal_mode_does_not_gate_status_lookup(orch: Orchestrator) -> None:
    resp = orch.run("lookup details for incident INC0000057", propose_writes=True)

    assert resp.route == ["incident"]
    assert resp.servicenow_write_proposal is None
    assert resp.incident is not None and resp.incident.action == "lookup"
    assert "State:" in resp.reply


def test_execute_approved_update_proposal_runs_incident_agent(orch: Orchestrator) -> None:
    proposal = {
        "operation": "update",
        "incident_number": "INC0010027",
        "delta": {"urgency": "3"},
        "summary": "update urgency for INC0010027 to low",
    }

    resp = orch.execute_approved_proposal(proposal)

    assert resp.route == ["incident"]
    assert resp.incident is not None and resp.incident.action == "update"
    assert resp.incident.fields_changed == {"urgency": "3"}
    assert "Updated incident INC0010027" in resp.reply


# ---------------------------------------------------------------------------
# Edge cases & failure modes.
# ---------------------------------------------------------------------------
def test_lookup_unknown_incident_reports_not_found(orch: Orchestrator) -> None:
    """A non-existent number surfaces IncidentNotFound as a clean 'not found'."""
    resp = orch.run("lookup details for incident INC9999999")

    assert resp.route == ["incident"], resp.route
    assert resp.incident is not None
    assert resp.incident.action == "lookup"
    assert resp.incident.ok is False
    assert "not found" in resp.reply.lower()


def test_update_unknown_incident_reports_not_found(orch: Orchestrator) -> None:
    resp = orch.run("update urgency for INC9999999 to low")

    assert resp.route == ["incident"], resp.route
    assert resp.incident is not None
    assert resp.incident.ok is False
    assert "not found" in resp.reply.lower()


def test_update_without_a_recognisable_field_is_reported(orch: Orchestrator) -> None:
    """An update that names an incident but no change is handled gracefully."""
    resp = orch.run("update INC0010027")

    assert resp.route == ["incident"], resp.route
    assert resp.incident is not None
    assert resp.incident.action == "update"
    assert resp.incident.ok is False
    assert "couldn't tell" in resp.reply.lower()


def test_unresolved_triage_escalates_to_incident_create(orch: Orchestrator) -> None:
    """Escalation handoff: unresolved triage + escalate wording -> create."""
    resp = orch.run("Escalate my Epic login problem")

    assert resp.route == ["triage", "incident"], resp.route
    assert resp.triage is not None
    assert resp.triage.resolved is False
    assert resp.triage.escalate_requested is True
    # An incident is created and assigned (assignment group comes from the KB).
    assert resp.incident is not None
    assert resp.incident.action == "create"
    assert resp.incident.ok is True
    assert resp.incident.incident is not None
    assert resp.incident.incident["assignment_group"]  # non-empty


def test_ambiguous_prompt_does_not_create_a_ticket(orch: Orchestrator) -> None:
    """A weak/ambiguous match neither resolves nor silently opens a ticket."""
    resp = orch.run("The quarterly TPS report is purple")

    assert resp.route == ["triage"], resp.route
    assert resp.incident is None, "must not auto-create a ticket on a weak match"
    assert resp.triage is not None and resp.triage.resolved is False
    # It offers to escalate rather than fabricating a resolution.
    assert "incident" in resp.reply.lower()


def test_empty_prompt_is_handled_without_crashing(orch: Orchestrator) -> None:
    resp = orch.run("")
    assert resp.route == ["triage"], resp.route
    assert resp.incident is None
    assert resp.triage is not None and resp.triage.resolved is False


@pytest.mark.parametrize(
    ("prompt", "expected_code", "expected_label"),
    [
        ("update urgency for INC0010027 to low", "3", "Low"),
        ("set urgency for INC0000057 to medium", "2", "Medium"),
        ("change urgency for INC0000057 to high", "1", "High"),
        ("set urgency for INC0000057 to critical", "1", "High"),
        ("set urgency for INC0000057 to moderate", "2", "Medium"),
    ],
)
def test_update_urgency_label_variants(
    orch: Orchestrator, prompt: str, expected_code: str, expected_label: str
) -> None:
    """Urgency label synonyms map to the correct ServiceNow numeric code."""
    resp = orch.run(prompt)
    assert resp.route == ["incident"], resp.route
    assert resp.incident is not None and resp.incident.ok is True
    assert resp.incident.fields_changed.get("urgency") == expected_code
    assert expected_label in resp.reply


# ---------------------------------------------------------------------------
# IncidentAgent intent detection (deterministic router used online + offline).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("prompt", "intent"),
    [
        ("lookup details for incident INC0000057", "lookup"),
        ("update urgency for INC0010027 to low", "update"),
        ("Unable to log into Epic. Create a new incident.", "create"),
        ("show me INC0000057", "lookup"),
        ("open a ticket for a broken printer", "create"),
    ],
)
def test_incident_agent_detects_intent(prompt: str, intent: str) -> None:
    assert IncidentAgent.detect_intent(prompt) == intent
