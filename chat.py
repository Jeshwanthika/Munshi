"""Talk to Munshi's agents in the terminal, with human approval.  Run:  python chat.py"""
import json
import uuid

import graph


def show(r):
    for t in r["trace"]:
        label = t.get("tool") or ""
        print(f"   [{t['agent']}] {t['event']} {label}".rstrip())
    while r["pending"]:
        p = r["pending"]
        print(f"\n   ⏸  APPROVAL NEEDED ({p['agent']} agent): {p['summary']}")
        print("   details:", json.dumps(p["args"], ensure_ascii=False))
        ans = input("   Approve? [y]es / [n]o / [e]dit: ").strip().lower()
        if ans.startswith("y"):
            r = graph.resume(SID, {"decision": "approve"})
        elif ans.startswith("e"):
            edits = json.loads(input('   JSON of fields to change, e.g. {"amount": 900}: ') or "{}")
            r = graph.resume(SID, {"decision": "edit", "args": edits})
        else:
            r = graph.resume(SID, {"decision": "reject", "reason": input("   Why? ")})
        for t in r["trace"]:
            print(f"   [{t['agent']}] {t['event']} {t.get('tool', '')}")
    print("\nMunshi:", r["reply"], "\n")


SID = str(uuid.uuid4())

if __name__ == "__main__":
    print("Munshi (multi-agent). Try: 'Bill Acme Corp $1200 for a landing page'. Type 'autopilot' or 'exit'.\n")
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("exit", "quit"):
            break
        if not text:
            continue
        try:
            show(graph.autopilot(SID) if text.lower() == "autopilot" else graph.run_turn(SID, text))
        except Exception as e:
            print(f"\n[error] {e}\n")
