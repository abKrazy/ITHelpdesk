"""Authoritative ServiceNow field / enum mapping for the incident integration.

This module is the single source of truth for how agent-facing intent (human
labels like ``"low"`` urgency, ``"resolved"`` state) maps onto ServiceNow Table
API values (``"3"``, ``"6"`` …) and back. Trinity's incident agent passes either
already-normalised values or human labels; everything is funnelled through here
so the wire format stays consistent.

References:
  * ``assets/ServiceNow-OpenAPI-spec.json`` -> ``TableRecord`` schema (field docs
    describe the enum encodings used below).
  * ARCHITECTURE.md §3.2–3.4 (create / lookup / update flows).
"""

from __future__ import annotations

# The Table API table backing helpdesk tickets.
TABLE_INCIDENT = "incident"

# --- Enum encodings (ServiceNow numeric strings) ---------------------------
# urgency / impact share the same 1=High .. 3=Low scale.
URGENCY_TO_VALUE = {"high": "1", "medium": "2", "low": "3"}
IMPACT_TO_VALUE = {"high": "1", "medium": "2", "low": "3"}
_VALUE_TO_URGENCY_LABEL = {"1": "High", "2": "Medium", "3": "Low"}

# incident.state choice list (default ServiceNow values).
STATE_TO_VALUE = {
    "new": "1",
    "in progress": "2",
    "on hold": "3",
    "resolved": "6",
    "closed": "7",
    "canceled": "8",
    "cancelled": "8",
}
_VALUE_TO_STATE_LABEL = {
    "1": "New",
    "2": "In Progress",
    "3": "On Hold",
    "6": "Resolved",
    "7": "Closed",
    "8": "Canceled",
}

# Fields whose *label* inputs get translated to ServiceNow numeric codes.
_ENUM_NORMALISERS = {
    "urgency": URGENCY_TO_VALUE,
    "impact": IMPACT_TO_VALUE,
    "state": STATE_TO_VALUE,
}


def _normalise(value: str, table: dict[str, str], *, field: str) -> str:
    """Return the ServiceNow code for ``value`` accepting a label or a raw code."""
    raw = str(value).strip()
    lowered = raw.lower()
    if lowered in table:
        return table[lowered]
    # Already a valid code (e.g. Trinity passed the mapped "3" directly).
    if raw in table.values():
        return raw
    valid = ", ".join(sorted(set(table) | set(table.values())))
    raise ValueError(f"Invalid {field} value {value!r}. Expected one of: {valid}.")


def normalize_urgency(value: str) -> str:
    """low/medium/high (or 1/2/3) -> ServiceNow urgency code."""
    return _normalise(value, URGENCY_TO_VALUE, field="urgency")


def normalize_impact(value: str) -> str:
    """low/medium/high (or 1/2/3) -> ServiceNow impact code."""
    return _normalise(value, IMPACT_TO_VALUE, field="impact")


def normalize_state(value: str) -> str:
    """State label (or numeric code) -> ServiceNow state code."""
    return _normalise(value, STATE_TO_VALUE, field="state")


def urgency_label(value: str) -> str:
    """ServiceNow urgency code -> human label (falls through unknown values)."""
    return _VALUE_TO_URGENCY_LABEL.get(str(value).strip(), str(value))


def state_label(value: str) -> str:
    """ServiceNow state code -> human label (falls through unknown values)."""
    return _VALUE_TO_STATE_LABEL.get(str(value).strip(), str(value))


def normalize_fields(fields: dict[str, str]) -> dict[str, str]:
    """Normalise a free-form update payload before sending to ServiceNow.

    Enum-bearing fields (urgency / impact / state) are translated from labels to
    codes; every other field passes through untouched (short_description,
    assignment_group, work_notes, comments, …).
    """
    out: dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        table = _ENUM_NORMALISERS.get(key)
        out[key] = _normalise(value, table, field=key) if table else str(value)
    return out


def build_create_payload(
    short_description: str,
    description: str = "",
    assignment_group: str = "",
    urgency: str = "3",
    *,
    impact: str | None = None,
) -> dict[str, str]:
    """Assemble (and normalise) the POST body for a new incident."""
    payload: dict[str, str] = {"short_description": short_description}
    if description:
        payload["description"] = description
    if assignment_group:
        payload["assignment_group"] = assignment_group
    if urgency:
        payload["urgency"] = normalize_urgency(urgency)
    if impact:
        payload["impact"] = normalize_impact(impact)
    return payload


def number_query(number: str) -> str:
    """Build the ``sysparm_query`` used to look an incident up by its number."""
    return f"number={number.strip().upper()}"
