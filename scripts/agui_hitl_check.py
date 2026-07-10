"""Multi-turn AG-UI HITL check: reach the ServiceNow write proposal -> interrupt,
then resume with approval and confirm the write executes.
"""
import json
import sys
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://app-ztk6zx5aedqtc.azurewebsites.net"
AGUI = BASE.rstrip("/") + "/agui"

thread_id = "t-" + uuid.uuid4().hex[:12]


def post(messages, resume=None, label=""):
    body = {
        "threadId": thread_id,
        "runId": "r-" + uuid.uuid4().hex[:12],
        "messages": messages,
        "tools": [],
        "context": [],
        "state": {},
        "forwardedProps": ({"resume": resume} if resume else {}),
    }
    text, tools, finished, err = [], [], None, None
    types = {}
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
            types[t] = types.get(t, 0) + 1
            if t == "TEXT_MESSAGE_CONTENT":
                text.append(ev.get("delta", ""))
            elif t == "TOOL_CALL_START":
                nm = ev.get("toolCallName") or ev.get("name")
                if nm:
                    tools.append(nm)
            elif t == "RUN_FINISHED":
                finished = ev
            elif t == "RUN_ERROR":
                err = ev
    interrupts = []
    if finished and isinstance(finished.get("outcome"), dict):
        interrupts = finished["outcome"].get("interrupts", []) or []
    print(f"\n===== {label} =====")
    print(f"HTTP {status}  types={types}")
    print(f"tools: {tools}")
    print(f"text ({len(''.join(text))}): {''.join(text).strip()[:500]}")
    print(f"interrupts: {len(interrupts)}")
    for it in interrupts:
        print("  interrupt:", json.dumps(it)[:600])
    if err:
        print("RUN_ERROR:", json.dumps(err)[:400])
    return {"status": status, "tools": tools, "text": "".join(text),
            "interrupts": interrupts, "err": err}


u1 = {"id": "m1", "role": "user",
      "content": "My laptop is very slow and affecting my work. Please file a ticket."}
r1 = post([u1], label="TURN 1: request ticket (expect triage)")

# Carry assistant reply + a follow-up that should trigger the write proposal.
a1 = {"id": "a1", "role": "assistant", "content": r1["text"] or "(troubleshooting steps)"}
u2 = {"id": "m2", "role": "user",
      "content": "I already tried all of those and nothing worked. Please go ahead and create "
                 "the incident now and assign it to Desktop Support."}
r2 = post([u1, a1, u2], label="TURN 2: insist -> expect WRITE PROPOSAL interrupt")

approved = False
if r2["interrupts"]:
    it = r2["interrupts"][0]
    interrupt_id = it.get("interruptId") or it.get("id")
    # rc8 function_approval_request carries the proposal in the tool-call args.
    fc = (((it.get("metadata") or {}).get("agent_framework") or {})
          .get("function_call") or {})
    args = fc.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    proposal_json = args.get("proposal_json")
    print(f"[extracted proposal_json len={len(proposal_json) if proposal_json else 0}]")
    resume = [{
        "interruptId": interrupt_id,
        "status": "resolved",
        # Match the frontend shape exactly: approved + proposal_json string.
        "payload": {"approved": True, "proposal_json": proposal_json},
    }]
    r3 = post([u1, a1, u2], resume=resume, label="TURN 3: APPROVE -> expect real ServiceNow write")
    approved = r3["status"] == 200 and not r3["err"]

print("\n===== HITL SUMMARY =====")
print(f"Turn2 produced write interrupt: {bool(r2['interrupts'])}")
print(f"Turn3 approval executed cleanly: {approved}")
