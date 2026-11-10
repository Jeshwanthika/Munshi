"""
Every tool Munshi's agents can use, which agent may use it, and which ones need human approval.
Rule: reading data never needs approval; anything that contacts a client or moves money does.
"""
import json
from datetime import date, datetime

import db
import ledger
import paypal_client as pp
import rag
import tax

WRITE_TOOLS = {"create_and_send_invoice", "send_reminder"}  # paused for human approval


def _nullable(t):
    return {"type": [t, "null"]}


SPECS = {
    "create_and_send_invoice": {
        "description": "Create a PayPal invoice and email it to the client. Requires human approval.",
        "parameters": {"type": "object", "properties": {
            "client_name": {"type": "string", "description": "Client or company name only, e.g. 'Acme Corp'. Never include verbs like 'Bill'."},
            "client_email": {"type": "string"},
            "description": {"type": "string", "description": "What the work was"},
            "amount": {"type": "number"},
            "currency": {**_nullable("string"), "description": "USD, EUR, GBP, AUD or CAD. Default USD"},
            "due_in_days": {**_nullable("integer"), "description": "Default 15"},
            "note": {**_nullable("string"), "description": "Invoice note incl. RBI purpose code and LUT line"},
        }, "required": ["client_name", "client_email", "description", "amount"]}},
    "list_invoices": {
        "description": "List all PayPal invoices with status (SENT, UNPAID, PAID, ...), amounts and due dates.",
        "parameters": {"type": "object", "properties": {}}},
    "send_reminder": {
        "description": "Send a payment reminder email for an unpaid PayPal invoice. Requires human approval.",
        "parameters": {"type": "object", "properties": {
            "invoice_id": {"type": "string"},
            "note": {"type": "string", "description": "The reminder message to the client"},
        }, "required": ["invoice_id", "note"]}},
    "find_invoices_needing_followup": {
        "description": "Find unpaid invoices that are overdue or due within 3 days, with days overdue.",
        "parameters": {"type": "object", "properties": {}}},
    "get_payment_updates": {
        "description": "Invoices that became PAID since the last check, with INR value and suggested tax set-aside.",
        "parameters": {"type": "object", "properties": {}}},
    "get_income_and_tax": {
        "description": "This financial year's income in INR by source (PayPal + imported statements, duplicates removed), pending and overdue amounts, and the income-tax estimate.",
        "parameters": {"type": "object", "properties": {
            "extra_expenses_inr": {**_nullable("number"), "description": "Optional business expenses this FY, INR"},
        }}},
    "search_tax_rules": {
        "description": "Search Munshi's tax & compliance knowledge base (Section 58 presumptive scheme, advance tax, slabs & rebate, GST on exports & LUT, purpose codes & FIRC, ITR & records). Returns rule passages with citations.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The rule question in plain words"},
        }, "required": ["query"]}},
    "list_imported_entries": {
        "description": "Entries imported from Upwork/bank statements, including which bank credits were ignored as duplicates and why.",
        "parameters": {"type": "object", "properties": {}}},
}

AGENT_TOOLS = {
    "billing": ["create_and_send_invoice", "list_invoices"],
    "collections": ["find_invoices_needing_followup", "send_reminder", "get_payment_updates", "list_invoices"],
    "tax": ["get_income_and_tax", "search_tax_rules"],
    "statement": ["list_imported_entries", "get_income_and_tax"],
}


def specs_for(agent):
    return [{"type": "function", "function": {"name": n, **SPECS[n]}} for n in AGENT_TOOLS[agent]]


# ---------- implementations ----------

def _days_overdue(due):
    try:
        return (date.today() - datetime.fromisoformat(due[:10]).date()).days
    except (TypeError, ValueError):
        return None


def find_invoices_needing_followup():
    out = []
    for i in pp.list_invoices():
        if i["status"] not in ("SENT", "UNPAID", "PARTIALLY_PAID"):
            continue
        d = _days_overdue(i.get("due_date"))
        if d is not None and d >= -3:
            out.append({**i, "days_overdue": d,
                        "stage": "due soon" if d < 0 else "overdue"})
    return out or {"message": "No invoices need follow-up right now."}


def get_payment_updates():
    seen = set(json.loads(db.get_kv("seen_paid", "[]")))
    s = ledger.summary()
    pct = s["tax"]["set_aside_percent_of_each_payment"]
    new = [i for i in s["invoices"] if i["status"] in ledger.PAID and i["invoice_id"] not in seen]
    db.set_kv("seen_paid", json.dumps(sorted(seen | {i["invoice_id"] for i in new})))
    return {"new_payments": [{"invoice_id": i["invoice_id"], "client": i["client"],
                              "amount": f'{i["currency"]} {i["amount"]}', "amount_inr": i["amount_inr"],
                              "set_aside_inr": round(i["amount_inr"] * pct / 100)} for i in new],
            "set_aside_percent": pct}


def get_income_and_tax(extra_expenses_inr=0):
    s = ledger.summary()
    if extra_expenses_inr:
        s["tax"] = tax.estimate(s["received_inr"], s["fees_inr"] + float(extra_expenses_inr))
    return {"usd_inr_rate_used": s["usd_inr"], "received_inr_by_source": s["by_source"],
            "total_received_inr": s["received_inr"], "pending_invoices_inr": s["pending_inr"],
            "platform_fees_inr": s["fees_inr"],
            "overdue_invoices": [{k: i[k] for k in ("invoice_id", "client", "amount", "currency", "due_date")}
                                 for i in s["overdue"]],
            "duplicates_ignored": len([e for e in s["entries"] if e["duplicate_of"]]),
            "received_inr_by_client_type": s["by_client_type"],
            "tds_credit_inr": s["tds_credit_inr"],
            "credits_waiting_for_user_review": [{k: e[k] for k in ("date", "payer", "amount_inr")} for e in s["needs_review"]],
            "gst": s["gst"],
            "tax": s["tax"]}


def list_imported_entries():
    rows = db.all_entries()
    return [{k: r.get(k) for k in ("date", "source", "payer", "description", "amount", "currency", "amount_inr",
                                    "entry_type", "duplicate_of", "client_type", "needs_review", "included", "tds_inr")}
            for r in rows] \
        or {"message": "Nothing imported yet."}


IMPL = {
    "create_and_send_invoice": lambda **a: pp.create_and_send_invoice(**a),
    "list_invoices": lambda **a: pp.list_invoices(),
    "send_reminder": lambda **a: pp.send_reminder(**a),
    "find_invoices_needing_followup": lambda **a: find_invoices_needing_followup(),
    "get_payment_updates": lambda **a: get_payment_updates(),
    "get_income_and_tax": lambda **a: get_income_and_tax(**a),
    "list_imported_entries": lambda **a: list_imported_entries(),
    "search_tax_rules": lambda **a: rag.search_tax_rules(a.get("query", ""), k=4),
}


def args_hash(args):
    import hashlib
    clean = {k: v for k, v in (args or {}).items() if v is not None}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()



def run_tool(name, args, approval_id=None):
    """The ONLY way any agent, endpoint or MCP client can execute a tool.
    Write tools run only with a recorded human approval for these exact arguments, used once."""
    args = {k: v for k, v in (args or {}).items() if v is not None}
    if name not in IMPL:
        return {"error": f"unknown tool {name}"}
    if name in WRITE_TOOLS:
        if approval_id is None or not db.consume_approval(approval_id, name, args_hash(args)):
            return {"error": "BLOCKED: this action needs a valid human approval for these exact details."}
    try:
        return IMPL[name](**args)
    except Exception as e:  # report errors back to the model so it can explain them
        return {"error": str(e)}


# ---------- validation guardrails (run BEFORE the human sees an approval card) ----------

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CURRENCIES = {"USD", "EUR", "GBP", "AUD", "CAD"}
MAX_INVOICE = 25_000          # sanity cap per invoice (in invoice currency)
LUT_LINE = "Export of services - supply meant for export under LUT without payment of IGST."
OPEN_STATUSES = ("SENT", "UNPAID", "PARTIALLY_PAID")


def validate(name, args):
    """Returns (clean_args, errors). Fixes what it safely can, reports what it can't."""
    a = {k: v for k, v in (args or {}).items() if v is not None}
    errors = []
    if name == "create_and_send_invoice":
        a["client_name"] = re.sub(r"^(bill|invoice|charge)\s+", "", str(a.get("client_name", "")).strip(),
                                  flags=re.I)
        if not a["client_name"]:
            errors.append("client_name is missing")
        if not EMAIL_RE.match(str(a.get("client_email", ""))):
            errors.append(f"client_email '{a.get('client_email')}' is not a valid email - ask the user")
        try:
            a["amount"] = round(float(a.get("amount")), 2)
            if not 0 < a["amount"] <= MAX_INVOICE:
                errors.append(f"amount must be between 0 and {MAX_INVOICE:,}")
        except (TypeError, ValueError):
            errors.append("amount is not a number")
        a["currency"] = str(a.get("currency") or "USD").upper()
        if a["currency"] not in CURRENCIES:
            errors.append(f"currency must be one of {sorted(CURRENCIES)}")
        a["due_in_days"] = int(a.get("due_in_days") or 15)
        if not 1 <= a["due_in_days"] <= 90:
            errors.append("due_in_days must be 1-90")
        if not str(a.get("description", "")).strip():
            errors.append("description of the work is missing")
        note = str(a.get("note") or "").strip()
        if not re.search(r"P0\d{3}", note):   # compliance: RBI purpose code must be on the invoice
            note = (note + " Purpose code: P0899.").strip()
        if "LUT" not in note:
            note = f"{note} {LUT_LINE}"
        a["note"] = note
    elif name == "send_reminder":
        inv = next((i for i in pp.list_invoices() if i["invoice_id"] == a.get("invoice_id")), None)
        if not inv:
            errors.append(f"invoice {a.get('invoice_id')} does not exist - use an ID from the tools")
        elif inv["status"] not in OPEN_STATUSES:
            errors.append(f"invoice {inv['invoice_id']} is {inv['status']}; don't chase a client who doesn't owe money")
        if len(str(a.get("note", "")).strip()) < 20:
            errors.append("reminder note is too short")
    return a, errors


def describe_action(name, args):
    """Human-readable summary shown on the approval card."""
    if name == "create_and_send_invoice":
        return (f"Send a PayPal invoice for {args.get('currency') or 'USD'} {args.get('amount')} "
                f"to {args.get('client_name')} <{args.get('client_email')}> for "
                f"\"{args.get('description')}\", due in {args.get('due_in_days') or 15} days.")
    if name == "send_reminder":
        return f"Email a payment reminder for invoice {args.get('invoice_id')}."
    return f"{name}({json.dumps(args)})"
