"""Live AG-UI end-to-end verification against the deployed api /agui endpoint.

Exercises two turns:
  1. An ungated status/read-only query -> should stream, NOT interrupt.
  2. A file-a-ticket query -> should call triage + propose a write -> interrupt.

Prints a compact event summary for each.
"""
import json
import sys
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://app-ztk6zx5aedqtc.azurewebsites.net"
AGUI = BASE.rstrip("/") + "/agui"


def run_turn(prompt: str, label: str) -> dict:
    thread_id = "t-" + uuid.uuid4().hex[:12]
    run_id = "r-" + uuid.uuid4().hex[:12]
    body = {
        "threadId": thread_id,
        "runId": run_id,
        "messages": [{"id": "m-" + uuid.uuid4().hex[:8], "role": "user", "content": prompt}],
        "tools": [],
        "context": [],
        "state": {},
        "forwardedProps": {},
    }
    tool_calls = []
    text_chunks = []
    finished = None
    err = None
    seen_types = {}
    with httpx.stream("POST", AGUI, json=body, timeout=180.0,
                      headers={"Accept": "text/event-stream"}) as resp:
        status = resp.status_code
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            raw = line[len("data:"):].strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            t = ev.get("type", "?")
            seen_types[t] = seen_types.get(t, 0) + 1
            if t in ("TEXT_MESSAGE_CONTENT",):
                text_chunks.append(ev.get("delta", ""))
            elif t in ("TOOL_CALL_START", "TOOL_CALL_NAME"):
                nm = ev.get("toolCallName") or ev.get("name")
                if nm:
                    tool_calls.append(nm)
            elif t == "RUN_FINISHED":
                finished = ev
            elif t == "RUN_ERROR":
                err = ev

    interrupts = []
    if finished and isinstance(finished.get("outcome"), dict):
        interrupts = finished["outcome"].get("interrupts", []) or []

    print(f"\n===== {label} =====")
    print(f"HTTP {status}")
    print(f"event types: {seen_types}")
    print(f"tool calls: {tool_calls}")
    txt = "".join(text_chunks).strip()
    print(f"assistant text ({len(txt)} chars): {txt[:400]}")
    print(f"interrupts: {len(interrupts)}")
    if interrupts:
        for it in interrupts:
            print("  interrupt:", json.dumps(it)[:500])
    if err:
        print("RUN_ERROR:", json.dumps(err)[:400])
    return {"status": status, "tools": tool_calls, "text": txt,
            "interrupts": interrupts, "err": err, "types": seen_types}


if __name__ == "__main__":
    r1 = run_turn("What is the status of incident INC0010001?", "TURN 1: status (ungated)")
    r2 = run_turn("My laptop is running very slow and it's affecting my work. Please file a ticket.",
                  "TURN 2: file a ticket (triage + gated write)")

    print("\n===== SUMMARY =====")
    print(f"Turn1 ungated (no interrupt expected): interrupts={len(r1['interrupts'])} "
          f"tools={r1['tools']} err={bool(r1['err'])}")
    print(f"Turn2 gated  (interrupt expected)   : interrupts={len(r2['interrupts'])} "
          f"tools={r2['tools']} err={bool(r2['err'])}")
