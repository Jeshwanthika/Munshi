"""
Munshi's multi-agent brain, built with LangGraph.

    START -> supervisor --routes--> billing | collections | tax | statement
    each agent --(read tool)--> tools ------> back to the same agent
               --(write tool)-> approval ---> PAUSE (interrupt) for the human,
                                              then execute / reject -> back to agent
               --(no tool)----> END

Memory: a checkpointer keeps each conversation (thread) so follow-ups work and paused
approvals can be resumed later.
"""
import json
import operator
from datetime import date
from typing import Annotated, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import os
import sqlite3

try:
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:  # older langgraph
    from langgraph.checkpoint.memory import MemorySaver as InMemorySaver


def _checkpointer():
    """Durable workflow state: a paused approval survives a server restart.
    (This is workflow state, not long-term user memory.)"""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        return SqliteSaver(sqlite3.connect(os.getenv("MUNSHI_CHECKPOINTS", "checkpoints.db"),
                                           check_same_thread=False))
    except ImportError:
        return InMemorySaver()

import db
import llm
import tools

AGENTS = ["billing", "collections", "tax", "statement"]
MAX_STEPS = 10
HISTORY_TURNS = 6

COMMON = f"""You are part of Munshi, an AI accountant for Indian freelancers paid by foreign clients via PayPal.
Today is {date.today().isoformat()}. The Indian financial year runs 1 April - 31 March.
Never invent invoice IDs, amounts, statuses or tax figures: always get them from tools.
Reply briefly and warmly in short sentences or simple bullets (no markdown tables).
When you have everything needed for an action (invoice or reminder), call the tool straight away: do NOT
summarise it and ask "shall I send?" in text. The user gets an editable approval card, and that is the confirmation. Use rupees in Indian
format (e.g. ₹1,83,615) and lakh. Actions that email clients are reviewed by the user before they happen;
if the user rejects one, acknowledge it and ask what to change."""

PROMPTS = {
    "billing": COMMON + """
You are the BILLING agent. You create and send PayPal invoices.
- You need client name, client email, description of the work and amount. Ask for anything missing; don't guess emails.
- client_name is only the client's name (e.g. "Kestrel Health"), never "Bill Kestrel Health".
- Defaults: USD, due in 15 days.
- Always put in the note: the RBI purpose code (P0802 for software/web/app development, P0899 for other services
  such as design, writing, marketing, consulting) and the line
  "Export of services - supply meant for export under LUT without payment of IGST."
- After an invoice is sent, give the invoice ID and the pay link.
- Asked for a client's pay link / payment URL: call list_invoices, find that client's unpaid invoice(s) and give
  pay_link exactly as returned (with invoice ID and amount). If it's already PAID, say so: there's nothing to pay.
- INDIAN (domestic) clients: PayPal does not support domestic payments in India, so do NOT call
  create_and_send_invoice for a client in India or an amount in INR/rupees. Explain this in one line, then draft
  in chat: a short invoice text (client, work, amount, "GST @18% if you are GST-registered", your UPI ID placeholder)
  and a UPI payment request message the user can send. Say the payment will be counted once they import their
  bank statement in Statements.""",
    "collections": COMMON + """
You are the COLLECTIONS agent. You make sure the freelancer gets paid.
- Asked for a pay link / payment URL: give pay_link from list_invoices or find_invoices_needing_followup exactly
  as returned. Never build a link yourself.
- QUESTIONS vs ACTIONS: "who hasn't paid me?", "what's overdue?", "any late payments?" are QUESTIONS: answer with
  a short list (client, amount, due date, days late) and end with "Want me to send reminders?". Do NOT call
  send_reminder for a question. Call send_reminder only when the user explicitly asks you to remind, chase,
  follow up or send something, or in an AUTOPILOT RUN.
- Use find_invoices_needing_followup to see what's due or overdue; get_payment_updates for newly paid invoices.
- Reminder tone by days_overdue: due soon (negative) = friendly heads-up; 1-7 = polite nudge;
  8-20 = firm but courteous; over 20 = firm, mention you'd like to settle this week.
  Mention the invoice ID, amount and due date. Sign off as the freelancer, keep it under 80 words.
- Only mention a tax set-aside for NEW payments returned by get_payment_updates, using its set_aside_inr
  numbers exactly. If there are no new payments, just say so. (18% is the GST rate, not a set-aside.)
- Send reminders one invoice at a time. When done, summarise what was sent and any new payments
  (with the suggested tax set-aside).""",
    "tax": COMMON + """
You are the TAX ADVISOR agent. Most users are freelancers, not accountants.
- DEFAULT ANSWER = plain words, 3-5 short lines, no jargon and no section numbers: how much tax they'll owe this
  year (after any TDS), how much to keep aside from each payment (e.g. "about ₹1,700 from every ₹1 lakh"), when to
  pay, and one simple reason ("as a freelancer, only half of what you earn is taxed"). Then offer:
  "Want to see how it's worked out?"
- DETAILS ONLY WHEN ASKED (how / why / show the calculation / breakdown / for my CA): then walk through
  tax.calculation exactly as returned (projection, taxable income, slab lines, rebate or marginal relief, cess,
  then TDS), and you may use terms like presumptive scheme (Section 58), marginal relief, cess.
- NUMBERS come only from get_income_and_tax (a tested calculator). Never compute or adjust tax yourself.
- RULES come only from search_tax_rules. For questions about rules, eligibility, deadlines, GST, LUT, TDS,
  purpose codes or filing, call search_tax_rules first and answer ONLY from the passages returned, in plain words.
  Cite with the cite label, e.g. [Advance tax for freelancers (Tax Year 2026-27) › Due dates ...], and end with
  "Sources:".
- Multi-hop: before mentioning a related rule that is NOT in your passages, call search_tax_rules again for it.
  Not found -> leave it out. If the passages don't cover the question, say so and suggest asking a CA.
- For a breakdown of individual fees, payments or TDS, call list_imported_entries; never say you lack data before checking.""",
    "statement": COMMON + """
You are the STATEMENT agent. You explain imported Upwork/bank statement entries: what was counted as income,
what was a fee, which bank credits were ignored because they are payouts of money already counted
(duplicate_of says which), which are foreign vs domestic (client_type), any TDS deducted by Indian clients
(tds_inr: count the gross fee; TDS is a tax credit), and which credits are waiting for the user's review
(needs_review: true and included empty). Never decide yourself whether a reviewed credit is income: ask the user
to use the Count / Not income buttons on the Statements page.""",
}

ROUTER_PROMPT = """Route the user's latest message to ONE agent. Return JSON {"agent": "...", "why": "..."}.
- billing: create/send a new invoice, bill or charge a client, list invoices, a client's pay link / payment URL
- collections: who hasn't paid / overdue / late payments (questions), reminders and chasing clients (actions), new payments received
- tax: tax, income totals, how much to set aside, presumptive/44ADA/Sec 58, advance tax, GST, TDS, regime, ITR
- statement: uploaded statements, Upwork/bank/UPI entries, duplicates, review of uncertain credits, why something was or wasn't counted,
  and any BREAKDOWN / split / list of individual payments, fees or TDS ("split up the platform fees", "which payments make up X")
Use the conversation for context: a short follow-up ("yes", "make it $900", "his email is x") goes to the
agent that was handling the previous turn."""


class State(TypedDict, total=False):
    messages: Annotated[list, operator.add]
    trace: Annotated[list, operator.add]
    route: str
    force_route: Optional[str]
    steps: int


# ---------- helpers ----------

def _context(messages):
    """Earlier turns: only user/assistant text. Current turn: everything (incl. tool calls)."""
    user_idx = [i for i, m in enumerate(messages) if m["role"] == "user"]
    if not user_idx:
        return messages
    cur = user_idx[-1]
    start = user_idx[-HISTORY_TURNS] if len(user_idx) >= HISTORY_TURNS else 0
    earlier = [{"role": m["role"], "content": m["content"]} for m in messages[start:cur]
               if m["role"] in ("user", "assistant") and m.get("content")]
    return earlier + messages[cur:]


def _last_call(state):
    call = state["messages"][-1]["tool_calls"][0]
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    return call, call["function"]["name"], args


def _tool_msg(call, result):
    return {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, default=str)}


# ---------- nodes ----------

def supervisor(state):
    forced = state.get("force_route")
    if forced in AGENTS:
        return {"route": forced, "force_route": None,
                "trace": [{"agent": "supervisor", "event": f"autopilot → {forced}"}]}
    convo = [m for m in state["messages"] if m["role"] in ("user", "assistant") and m.get("content")][-6:]
    text = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in convo)
    out = llm.chat_json(ROUTER_PROMPT, text)
    route = out.get("agent") if out.get("agent") in AGENTS else "tax"
    return {"route": route, "trace": [{"agent": "supervisor", "event": f"→ {route}", "why": out.get("why", "")}]}


def make_agent(name):
    def agent_node(state):
        msgs = [{"role": "system", "content": PROMPTS[name]}] + _context(state["messages"])
        msg = llm.chat(msgs, tools=tools.specs_for(name))
        calls = (msg.get("tool_calls") or [])[:1]  # one action at a time keeps approvals simple
        out = {"role": "assistant", "content": msg.get("content") or ""}
        if calls:
            out["tool_calls"] = [{"id": c["id"], "type": "function",
                                  "function": {"name": c["function"]["name"],
                                               "arguments": c["function"].get("arguments") or "{}"}}
                                 for c in calls]
        return {"messages": [out], "steps": state.get("steps", 0) + 1}
    agent_node.__name__ = name
    return agent_node


def tools_node(state):
    call, name, args = _last_call(state)
    if name not in tools.AGENT_TOOLS[state["route"]]:
        result = {"error": f"{name} is not available to the {state['route']} agent"}
    else:
        result = tools.run_tool(name, args)
    return {"messages": [_tool_msg(call, result)],
            "trace": [{"agent": state["route"], "tool": name, "event": "used"}]}


def approval_node(state):
    call, name, args = _last_call(state)
    args, errors = tools.validate(name, args)
    if errors:  # guardrail: bounce back to the agent, the human never sees a broken action
        return {"messages": [_tool_msg(call, {"status": "validation_failed", "errors": errors})],
                "trace": [{"agent": state["route"], "tool": name, "event": "blocked by validation"}]}
    decision = interrupt({"tool": name, "args": args, "agent": state["route"],
                          "summary": tools.describe_action(name, args)})
    choice = (decision or {}).get("decision", "reject")
    if choice in ("approve", "edit"):
        final_args = {**args, **(decision.get("args") or {})} if choice == "edit" else args
        final_args, errors = tools.validate(name, final_args)  # edits are validated too
        if errors:
            result = {"status": "validation_failed", "errors": errors}
        else:
            approval_id = db.record_approval(name, tools.args_hash(final_args),
                                             json.dumps(final_args, default=str), choice)
            result = tools.run_tool(name, final_args, approval_id=approval_id)
            db.set_approval_result(approval_id, json.dumps(result, default=str)[:2000])
        event = "edited & approved" if choice == "edit" else "approved"
    else:
        db.record_approval(name, tools.args_hash(args), json.dumps(args, default=str), "reject")
        result = {"status": "rejected_by_user", "reason": (decision or {}).get("reason", "")}
        event = "rejected"
    return {"messages": [_tool_msg(call, result)],
            "trace": [{"agent": state["route"], "tool": name, "event": event}]}


def after_agent(state):
    last = state["messages"][-1]
    if not last.get("tool_calls") or state.get("steps", 0) >= MAX_STEPS:
        return END
    return "approval" if last["tool_calls"][0]["function"]["name"] in tools.WRITE_TOOLS else "tools"


def back_to_agent(state):
    return state["route"]


def build():
    g = StateGraph(State)
    g.add_node("supervisor", supervisor)
    for a in AGENTS:
        g.add_node(a, make_agent(a))
        g.add_conditional_edges(a, after_agent, ["tools", "approval", END])
    g.add_node("tools", tools_node)
    g.add_node("approval", approval_node)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", back_to_agent, AGENTS)
    g.add_conditional_edges("tools", back_to_agent, AGENTS)
    g.add_conditional_edges("approval", back_to_agent, AGENTS)
    return g.compile(checkpointer=_checkpointer())


GRAPH = build()


# ---------- public API ----------

def _cfg(thread_id):
    return {"configurable": {"thread_id": thread_id}}


def _trace_len(thread_id):
    return len(GRAPH.get_state(_cfg(thread_id)).values.get("trace", []))


def _result(thread_id, trace_before):
    snap = GRAPH.get_state(_cfg(thread_id))
    pending = None
    for t in snap.tasks:
        if getattr(t, "interrupts", None):
            pending = t.interrupts[0].value
            break
    vals = snap.values
    reply = ""
    if not pending:
        for m in reversed(vals.get("messages", [])):
            if m["role"] == "assistant" and m.get("content"):
                reply = m["content"]
                break
            if m["role"] == "user":
                break
    return {"reply": reply, "pending": pending, "trace": vals.get("trace", [])[trace_before:],
            "agent": vals.get("route")}


def run_turn(thread_id, text, force_route=None):
    before = _trace_len(thread_id)
    GRAPH.invoke({"messages": [{"role": "user", "content": text}], "steps": 0,
                  "force_route": force_route}, _cfg(thread_id))
    return _result(thread_id, before)


def resume(thread_id, decision):
    """decision = {"decision": "approve" | "edit" | "reject", "args": {...}, "reason": "..."}"""
    before = _trace_len(thread_id)
    GRAPH.invoke(Command(resume=decision), _cfg(thread_id))
    return _result(thread_id, before)


AUTOPILOT_MESSAGE = (
    "AUTOPILOT RUN (scheduled check, not typed by the user). "
    "1) Call get_payment_updates and note any new payments. "
    "2) Call find_invoices_needing_followup. "
    "3) For each invoice that needs follow-up, call send_reminder with a reminder in the right tone, one at a time. "
    "4) Finish with a short summary: payments received (with tax set-aside) and reminders sent or skipped.")


def autopilot(thread_id):
    return run_turn(thread_id, AUTOPILOT_MESSAGE, force_route="collections")
