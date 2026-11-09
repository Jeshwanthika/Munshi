"""Munshi's brain: a Groq-hosted LLM that calls PayPal + tax tools."""
import json
import os
from datetime import date

import requests
from dotenv import load_dotenv

import ledger
import paypal_client as pp
import tax

load_dotenv()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

SYSTEM = f"""You are Munshi, an AI accountant for Indian freelancers who get paid by foreign clients through PayPal.
Today is {date.today().isoformat()}. Indian financial year runs 1 April - 31 March.
You can: create and send PayPal invoices, list invoices and their status, send payment reminders, and estimate income tax.
Rules:
- Before creating an invoice you need client name, client email, what the work was, and amount. Ask for anything missing. Default currency USD, due in 15 days.
- Put the RBI purpose code in the invoice note: P0802 for software/web/app development, P0899 for other services (design, writing, marketing, consulting).
  Also add: "Export of services - supply meant for export under LUT without payment of IGST."
- Never invent invoice IDs, amounts or statuses: always use tools.
- When asked about tax, income, earnings or overdue payments, call get_income_and_tax. Mention any double-counted payouts it ignored. Explain the result in plain words, in rupees, using Indian number format (lakh).
- Reminders should be polite and short.
- Keep replies brief and friendly: short sentences or a simple bullet list, never markdown tables. Always include the pay link after creating an invoice. You give estimates, not legal tax advice."""

TOOLS = [
    {"type": "function", "function": {
        "name": "create_and_send_invoice",
        "description": "Create a PayPal invoice and email it to the client.",
        "parameters": {"type": "object", "properties": {
            "client_name": {"type": "string"},
            "client_email": {"type": "string"},
            "description": {"type": "string", "description": "What the work was"},
            "amount": {"type": "number"},
            "currency": {"type": ["string", "null"], "description": "USD, EUR, GBP, AUD or CAD"},
            "due_in_days": {"type": ["integer", "null"]},
            "note": {"type": ["string", "null"], "description": "Note on the invoice incl. purpose code"},
        }, "required": ["client_name", "client_email", "description", "amount"]}}},
    {"type": "function", "function": {
        "name": "list_invoices",
        "description": "List all PayPal invoices with status (DRAFT, SENT, UNPAID, PAID, etc.).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "send_reminder",
        "description": "Send a payment reminder for an unpaid invoice.",
        "parameters": {"type": "object", "properties": {
            "invoice_id": {"type": "string"},
            "note": {"type": "string", "description": "Polite reminder message"},
        }, "required": ["invoice_id", "note"]}}},
    {"type": "function", "function": {
        "name": "get_income_and_tax",
        "description": "Total this financial year's income in INR from PayPal and imported statements (Upwork, bank), overdue invoices, and income tax estimate.",
        "parameters": {"type": "object", "properties": {
            "expenses_inr": {"type": ["number", "null"],
                             "description": "Optional total business expenses so far this FY, in INR"},
        }}}},
]


def income_summary(expenses_inr=0.0):
    """All income this FY across PayPal + imported statements (double counting removed)."""
    s = ledger.summary()
    if expenses_inr:
        s["tax"] = tax.estimate(s["received_inr"], s["fees_inr"] + expenses_inr)
    return {"usd_inr_rate_used": s["usd_inr"], "received_inr_by_source": s["by_source"],
            "total_received_inr": s["received_inr"], "pending_invoices_inr": s["pending_inr"],
            "platform_fees_inr": s["fees_inr"],
            "overdue_invoices": [{k: i[k] for k in ("invoice_id", "client", "amount", "currency", "due_date")}
                                 for i in s["overdue"]],
            "duplicates_ignored": [e["description"] + " -> " + e["duplicate_of"]
                                   for e in s["entries"] if e["duplicate_of"]],
            "tax": s["tax"]}


def run_tool(name, args):
    args = {k: v for k, v in (args or {}).items() if v is not None}  # drop nulls from the model
    try:
        if name == "create_and_send_invoice":
            return pp.create_and_send_invoice(**args)
        if name == "list_invoices":
            return pp.list_invoices()
        if name == "send_reminder":
            return pp.send_reminder(**args)
        if name == "get_income_and_tax":
            return income_summary(float(args.get("expenses_inr") or 0))
        return {"error": f"unknown tool {name}"}
    except Exception as e:  # report errors back to the model so it can explain
        return {"error": str(e)}


def _groq(messages):
    r = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
        json={"model": MODEL, "messages": messages, "tools": TOOLS,
              "tool_choice": "auto", "temperature": 0.2},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Groq error ({r.status_code}): {r.text}")
    return r.json()["choices"][0]["message"]


def chat(history, user_text, on_tool=None):
    """Append user_text, run the tool loop, return the assistant's reply. Mutates history."""
    if not history:
        history.append({"role": "system", "content": SYSTEM})
    history.append({"role": "user", "content": user_text})
    for _ in range(6):
        msg = _groq(history)
        calls = msg.get("tool_calls") or []
        history.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        if not calls:
            return msg.get("content") or ""
        for c in calls:
            name = c["function"]["name"]
            args = json.loads(c["function"].get("arguments") or "{}")
            if on_tool:
                on_tool(name, args)
            result = run_tool(name, args)
            history.append({"role": "tool", "tool_call_id": c["id"],
                            "content": json.dumps(result, default=str)})
    return "Sorry, I got stuck. Please try rephrasing."
