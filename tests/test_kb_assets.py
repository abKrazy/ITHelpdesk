"""KB asset integrity — every article the flows depend on is well-formed.

The triage/escalation flows extract the "Recommended Assignment Group" from the
matching KB doc (ARCHITECTURE.md §3.1–3.2). If any ``assets/kb/*.md`` article is
missing a title, resolution steps, or an assignment group, escalation would
create incidents with an empty queue — a silent correctness bug. This test locks
the KB contract the agents rely on.
"""

from __future__ import annotations

from helpdesk.agents.kb import load_local_kb


def test_kb_docs_present() -> None:
    docs = load_local_kb()
    assert len(docs) >= 7, f"expected the shipped KB set, found {len(docs)}"


def test_every_kb_doc_has_title_steps_and_assignment_group() -> None:
    for doc in load_local_kb():
        assert doc.title, f"{doc.source}: missing H1 title"
        assert doc.assignment_group, f"{doc.source}: missing Recommended Assignment Group"
        assert doc.resolution_steps.strip(), f"{doc.source}: missing Resolution Steps"


def test_known_assignment_groups_map_to_expected_docs() -> None:
    """Spot-check the assignment groups the sample prompts route to."""
    by_id = {doc.doc_id: doc for doc in load_local_kb()}
    assert by_id["unable-to-login"].assignment_group == "Identity and Access Management"
    assert by_id["password-reset"].assignment_group == "Service Desk"
    assert by_id["vpn-connectivity"].assignment_group == "Network Support"
