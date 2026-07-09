"""Offline tests for :class:`helpdesk.shared.config.Settings`.

Covers the model-deployment wiring: the triage agent may run on its own
(smaller/cheaper) deployment via ``AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT`` while
the orchestrator + incident agents stay on the main
``AZURE_OPENAI_CHAT_DEPLOYMENT``. When the triage-specific var is unset, triage
falls back to the main deployment so nothing breaks.
"""

from __future__ import annotations

from helpdesk.shared.config import Settings


def test_triage_deployment_uses_dedicated_var_when_set() -> None:
    s = Settings(
        {
            "AZURE_OPENAI_CHAT_DEPLOYMENT": "gpt-5.4",
            "AZURE_OPENAI_TRIAGE_CHAT_DEPLOYMENT": "gpt-5.4-mini",
        }
    )
    assert s.chat_deployment == "gpt-5.4"
    assert s.triage_chat_deployment == "gpt-5.4-mini"


def test_triage_deployment_falls_back_to_main_when_unset() -> None:
    s = Settings({"AZURE_OPENAI_CHAT_DEPLOYMENT": "gpt-5.4"})
    assert s.triage_chat_deployment == "gpt-5.4"


def test_triage_deployment_empty_when_nothing_configured() -> None:
    s = Settings({})
    assert s.triage_chat_deployment == ""
