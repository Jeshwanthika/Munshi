"""
Munshi's ledger: merges PayPal invoices with imported statements, removes double counting,
and produces the numbers for the dashboard and the AI agent.
"""
import json
import os
from datetime import date, datetime

import requests
from dotenv import load_dotenv

import db
import paypal_client as pp
import tax

load_dotenv()

USD_INR = float(os.getenv("USD_INR", "88"))
FX = {"INR": 1.0, "USD": USD_INR, "EUR": USD_INR * 1.08, "GBP": USD_INR * 1.27,
      "AUD": USD_INR * 0.65, "CAD": USD_INR * 0.72}
PAID = ("PAID", "MARKED_AS_PAID")
MATCH_TOLERANCE = 0.12   # bank credit can be up to 12% below gross (platform fees like Upwork 10%, PayPal ~4.4%, FX)
MATCH_DAYS = 30


def to_inr(amount, currency):
    return float(amount) * FX.get((currency or "USD").upper(), USD_INR)


def _d(s):
    try:
        return datetime.fromisoformat(str(s)[:10]).date()
    except ValueError:
        return date.today()


# ---------- Statement import (AI extraction) ----------

EXTRACT_PROMPT = """You read financial statements of an Indian freelancer and extract money entries.
Return JSON: {"source": "<platform or bank name, e.g. Upwork, HDFC Bank>",
 "source_kind": "platform" or "bank",
 "entries": [{"date": "YYYY-MM-DD", "payer": "...", "description": "...",
              "amount": <positive number>, "currency": "USD/INR/...", "entry_type": "income" or "fee"}]}
Rules: include client payments received as "income" (use the GROSS amount before fees).
Include platform service fees / payment fees as "fee". Ignore withdrawals to own bank account, personal
spending, salary refunds, and transfers between own accounts. For a bank statement, include only deposits
(credits) that look like client or platform/payment-gateway payouts (PayPal, Upwork, Payoneer, Wise, Stripe, SWIFT inward)."""


def extract_with_ai(text):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
        json={"model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
              "messages": [{"role": "system", "content": EXTRACT_PROMPT},
                           {"role": "user", "content": text[:15000]}],
              "response_format": {"type": "json_object"}, "temperature": 0},
        timeout=90,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Groq error ({r.status_code}): {r.text}")
    return json.loads(r.json()["choices"][0]["message"]["content"])


def find_duplicate(entry, paypal_paid, existing):
    """A bank credit is a duplicate if it is the payout of money already counted elsewhere."""
    if entry["source_kind"] != "bank" or entry["entry_type"] != "income":
        return None
    credit, when = entry["amount_inr"], _d(entry["date"])
    candidates = [(f"PayPal invoice {i['invoice_id']} ({i['client']})", i["amount_inr"], i.get("invoice_date"))
                  for i in paypal_paid]
    candidates += [(f"{e['source']} payment from {e['payer']}", e["amount_inr"], e["date"])
                   for e in existing if e["source_kind"] == "platform" and e["entry_type"] == "income"]
    for label, gross, d in candidates:
        if gross and gross * (1 - MATCH_TOLERANCE) <= credit <= gross * 1.01 \
                and -2 <= (when - _d(d)).days <= MATCH_DAYS:  # payout lands after the payment
            return label
    return None


def import_statement(text):
    data = extract_with_ai(text)
    source = data.get("source") or "Statement"
    kind = data.get("source_kind") if data.get("source_kind") in ("bank", "platform") else "platform"
    paypal_paid = [i for i in paypal_invoices() if i["status"] in PAID]
    existing = db.all_entries()
    added = []
    for e in data.get("entries", []):
        try:
            amount = abs(float(e.get("amount", 0)))
        except (TypeError, ValueError):
            continue
        if not amount:
            continue
        row = {"date": str(e.get("date") or date.today())[:10], "payer": e.get("payer") or "",
               "description": e.get("description") or "", "amount": amount,
               "currency": (e.get("currency") or "INR").upper(),
               "source": source, "source_kind": kind,
               "entry_type": "fee" if e.get("entry_type") == "fee" else "income"}
        row["amount_inr"] = round(to_inr(amount, row["currency"]))
        row["duplicate_of"] = find_duplicate(row, paypal_paid, existing)
        if db.add(row):
            added.append(row)
            existing.append(row)
    return {"source": source, "source_kind": kind, "added": added,
            "duplicates_caught": [a for a in added if a["duplicate_of"]]}


# ---------- Summary ----------

def paypal_invoices():
    out = []
    for i in pp.list_invoices():
        if i.get("amount"):
            i["amount_inr"] = round(to_inr(i["amount"], i["currency"]))
            out.append(i)
    return out


def summary():
    fy_start, fy_end = tax.financial_year()
    in_fy = lambda d: fy_start <= _d(d) <= fy_end
    invoices = paypal_invoices()
    entries = db.all_entries()

    by_source = {}
    paypal_received = sum(i["amount_inr"] for i in invoices
                          if i["status"] in PAID and in_fy(i.get("invoice_date") or date.today()))
    if paypal_received:
        by_source["PayPal"] = paypal_received
    fees = 0
    for e in entries:
        if not in_fy(e["date"]) or e["duplicate_of"]:
            continue
        if e["entry_type"] == "fee":
            fees += e["amount_inr"]
        else:
            by_source[e["source"]] = by_source.get(e["source"], 0) + e["amount_inr"]

    received = sum(by_source.values())
    pending = sum(i["amount_inr"] for i in invoices if i["status"] in ("SENT", "UNPAID", "PARTIALLY_PAID"))
    overdue = [i for i in invoices if i["status"] in ("SENT", "UNPAID")
               and i.get("due_date") and _d(i["due_date"]) < date.today()]
    return {
        "usd_inr": USD_INR,
        "received_inr": received,
        "pending_inr": pending,
        "fees_inr": round(fees),
        "by_source": by_source,
        "invoices": invoices,
        "entries": entries,
        "overdue": overdue,
        "tax": tax.estimate(received, fees),
    }
