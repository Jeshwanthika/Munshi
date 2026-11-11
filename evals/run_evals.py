"""
Munshi eval suite.

    python evals/run_evals.py            # everything (calls Groq; PayPal is MOCKED, nothing is sent)
    python evals/run_evals.py --offline  # only the deterministic suites (no API calls)

Suites
  A  Tax engine correctness        deterministic   slabs, rebate, marginal relief, presumptive vs regular
  B  Guardrails & enforcement      deterministic   validation, approval tokens, replay/tamper, webhook idempotency, dedup
  C  RAG retrieval                 deterministic   right rule section in top-1 / top-3
  D  Supervisor routing            LLM             message -> correct specialist agent
  E  Agent behaviour + HITL        LLM             right tool, right arguments, pauses for approval, never sends unapproved
  F  RAG faithfulness              LLM-as-judge    answers grounded in retrieved rules, cited, abstains when unknown
Writes evals/report.md and evals/results.json.
"""
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# Isolated, throw-away databases so evals never touch your real data
TMP = tempfile.mkdtemp(prefix="munshi-evals-")
os.environ["MUNSHI_DB"] = os.path.join(TMP, "evals.db")
os.environ["MUNSHI_CHECKPOINTS"] = os.path.join(TMP, "checkpoints.db")
os.environ.setdefault("FREELANCER_TYPE", "profession")

OFFLINE = "--offline" in sys.argv

import db  # noqa: E402
import ledger  # noqa: E402
import paypal_client as pp  # noqa: E402
import rag  # noqa: E402
import tax  # noqa: E402
import tools  # noqa: E402

# ---------------------------------------------------------------- mocked PayPal
TODAY = date.today()
MOCK_INVOICES = [
    {"invoice_id": "INV2-PAID-0001", "status": "PAID", "client": "Acme Corp", "client_email": "ap@acme.com",
     "currency": "USD", "amount": "1200.00", "invoice_date": (TODAY - timedelta(days=20)).isoformat(),
     "due_date": (TODAY - timedelta(days=5)).isoformat()},
    {"invoice_id": "INV2-OPEN-0002", "status": "SENT", "client": "Nordwind GmbH", "client_email": "pay@nordwind.de",
     "currency": "USD", "amount": "525.00", "invoice_date": (TODAY - timedelta(days=25)).isoformat(),
     "due_date": (TODAY - timedelta(days=12)).isoformat()},
    {"invoice_id": "INV2-OPEN-0003", "status": "SENT", "client": "Harbor Coffee", "client_email": "hi@harbor.ca",
     "currency": "USD", "amount": "350.00", "invoice_date": (TODAY - timedelta(days=13)).isoformat(),
     "due_date": (TODAY + timedelta(days=2)).isoformat()},
]
SENT = []  # every call that would have reached PayPal


def _mock_create(**a):
    SENT.append(("invoice", a))
    return {"invoice_id": "INV2-EVAL-" + uuid.uuid4().hex[:4].upper(), "pay_link": "https://example.test/pay"}


def _mock_remind(**a):
    SENT.append(("reminder", a))
    return {"invoice_id": a.get("invoice_id"), "reminder_sent": True}


for _i in MOCK_INVOICES:  # unpaid invoices carry a pay link, like the real list_invoices
    _i["pay_link"] = f"https://www.sandbox.paypal.com/invoice/p/#{_i['invoice_id']}" if _i["status"] == "SENT" else None
pp.list_invoices = lambda page_size=50: [dict(i) for i in MOCK_INVOICES]
pp.create_and_send_invoice = _mock_create
pp.send_reminder = _mock_remind

# ---------------------------------------------------------------- harness
RESULTS = []


def check(suite, name, passed, detail=""):
    RESULTS.append({"suite": suite, "case": name, "passed": bool(passed), "detail": str(detail)[:300]})
    print(f"  {'PASS' if passed else 'FAIL'}  [{suite}] {name}" + ("" if passed else f"  -> {detail}"))


def safe(suite, name, fn):
    try:
        fn()
    except Exception as e:  # a crash is a failed case, not a crashed suite
        check(suite, name, False, f"error: {e}")


# ================================================================ A. Tax engine
def suite_a():
    print("\nA. Tax engine")
    cases = [  # (taxable income, expected total tax incl. 4% cess)
        (400_000, 0), (1_200_000, 0),                      # rebate up to 12L
        (1_210_000, round(10_000 * 1.04)),                 # marginal relief: tax capped at income above 12L
        (1_600_000, round(120_000 * 1.04)),                # 20k + 40k + 60k
        (2_500_000, round(330_000 * 1.04)),                # through the 30% slab
    ]
    for inc, exp in cases:
        got = tax.total_tax(inc)
        check("A", f"tax on ₹{inc:,}", got == exp, f"expected {exp}, got {got}")

    nov = date(2026, 11, 9)  # 8 months into FY 2026-27
    e = tax.estimate(1_100_000, 150_000, today=nov, kind="profession")
    check("A", "projection scales 8 months to 12", e["projected_annual_receipts_inr"] == 1_650_000, e["projected_annual_receipts_inr"])
    check("A", "presumptive 50% → ₹0 tax on ₹16.5L", e["tax_if_presumptive_inr"] == 0, e)
    check("A", "regular method tax on ₹16.5L − expenses", e["tax_if_regular_inr"] == 97_500, e["tax_if_regular_inr"])
    check("A", "recommends presumptive", e["recommended"] == "presumptive", e["recommended"])
    check("A", "zero tax → no advance tax needed", "No advance tax" in e["advance_tax_deadline"], e["advance_tax_deadline"])
    hi = tax.estimate(2_400_000, 0, today=nov, kind="profession")
    check("A", "presumptive deadline is 15 March", "15 March 2027" in hi["advance_tax_deadline"], hi["advance_tax_deadline"])
    t = tax.estimate(2_400_000, 0, today=nov, tds_inr=30_000)
    check("A", "TDS credit reduces tax still to pay", t["tax_after_tds_inr"] == t["estimated_annual_tax_inr"] - 30_000, t)
    r = tax.estimate(1_000_000, 0, today=nov, tds_inr=12_000)
    check("A", "TDS above tax → refund due", r["refund_due_inr"] == 12_000 and r["tax_after_tds_inr"] == 0, r)
    b = tax.estimate(1_100_000, 0, today=nov, kind="business")
    check("A", "business scheme uses 6%", b["presumptive_rate"] == 0.06 and b["tax_if_presumptive_inr"] == 0, b)
    big = tax.estimate(6_000_000, 0, today=nov)       # projects to 90L > 75L limit
    check("A", "above ₹75L: presumptive not eligible", big["presumptive_eligible"] is False and big["recommended"] == "regular", big)
    check("A", "assumptions are reported", len(e.get("assumptions", [])) >= 4)
    w = tax.estimate(1_665_131, 99_100, today=date(2026, 11, 11), tds_inr=9000)
    pw = w["calculation"]["presumptive"]
    check("A", "working: marginal relief case reproduces ₹50,802",
          pw["slab_tax"] == 67_327 and pw["tax_after_relief"] == 48_848 and pw["total"] == 50_802 == w["estimated_annual_tax_inr"], pw)
    check("A", "working: slab lines add up to slab tax", sum(l["tax"] for l in pw["slabs"]) == pw["slab_tax"])
    check("A", "working: regular method matches", w["calculation"]["regular"]["total"] == w["tax_if_regular_inr"] == 298_752)
    check("A", "Indian number format", tax.inr(2_497_696) == "₹24,97,696" and tax.inr(12_345_678) == "₹1,23,45,678")


# ================================================================ B. Guardrails & enforcement
def suite_b():
    print("\nB. Guardrails & enforcement")
    v = tools.validate
    a, err = v("create_and_send_invoice", {"client_name": "Bill Kestrel Health", "client_email": "k@health.io",
                                            "description": "app", "amount": 1500})
    check("B", "strips verb from client name", a["client_name"] == "Kestrel Health" and not err, a)
    check("B", "adds RBI purpose code + LUT line", "P0" in a["note"] and "LUT" in a["note"], a["note"])
    for name, bad, needle in [
        ("blocks invalid email", {"client_email": "not-an-email"}, "email"),
        ("blocks zero amount", {"amount": 0}, "amount"),
        ("blocks huge amount", {"amount": 999_999}, "amount"),
        ("blocks unsupported currency", {"currency": "JPY"}, "currency"),
        ("blocks empty description", {"description": ""}, "description"),
    ]:
        base = {"client_name": "X", "client_email": "x@y.com", "description": "work", "amount": 100, **bad}
        _, err = v("create_and_send_invoice", base)
        check("B", name, any(needle in e for e in err), err)
    _, err = v("send_reminder", {"invoice_id": "INV2-PAID-0001", "note": "Friendly reminder about your invoice."})
    check("B", "blocks reminder for PAID invoice", any("PAID" in e for e in err), err)
    _, err = v("send_reminder", {"invoice_id": "INV2-FAKE-9999", "note": "Friendly reminder about your invoice."})
    check("B", "blocks reminder for unknown invoice", any("does not exist" in e for e in err), err)
    _, err = v("send_reminder", {"invoice_id": "INV2-OPEN-0002", "note": "Friendly reminder about your invoice."})
    check("B", "allows reminder for open invoice", not err, err)

    args = {"client_name": "K", "client_email": "k@x.com", "description": "d", "amount": 100.0,
            "currency": "USD", "due_in_days": 15, "note": "n"}
    before = len(SENT)
    r = tools.run_tool("create_and_send_invoice", args)
    check("B", "write tool without approval is BLOCKED", "BLOCKED" in str(r) and len(SENT) == before, r)
    aid = db.record_approval("create_and_send_invoice", tools.args_hash(args), "{}", "approve")
    r = tools.run_tool("create_and_send_invoice", {**args, "amount": 9999.0}, approval_id=aid)
    check("B", "approval for different amount is BLOCKED", "BLOCKED" in str(r) and len(SENT) == before, r)
    r = tools.run_tool("create_and_send_invoice", args, approval_id=aid)
    check("B", "valid approval executes", len(SENT) == before + 1, r)
    r = tools.run_tool("create_and_send_invoice", args, approval_id=aid)
    check("B", "approval cannot be replayed", "BLOCKED" in str(r) and len(SENT) == before + 1, r)
    rid = db.record_approval("create_and_send_invoice", tools.args_hash(args), "{}", "reject")
    r = tools.run_tool("create_and_send_invoice", args, approval_id=rid)
    check("B", "rejection cannot be used as approval", "BLOCKED" in str(r), r)
    check("B", "webhook events are idempotent",
          db.save_webhook_event("WH-E1", "INVOICING.INVOICE.PAID", "X", "{}") is True
          and db.save_webhook_event("WH-E1", "INVOICING.INVOICE.PAID", "X", "{}") is False)

    paid = [{**MOCK_INVOICES[0], "amount_inr": round(1200 * ledger.USD_INR)}]
    pay_day = (TODAY - timedelta(days=18)).isoformat()
    bank = {"date": pay_day, "amount_inr": round(1200 * ledger.USD_INR * 0.956), "source_kind": "bank", "entry_type": "income"}
    check("B", "dedup: PayPal payout in bank is caught", ledger.find_duplicate(bank, paid, []) is not None)
    other = {**bank, "amount_inr": round(490 * ledger.USD_INR)}
    check("B", "dedup: unrelated client payment is kept", ledger.find_duplicate(other, paid, []) is None)
    early = {**bank, "date": (TODAY - timedelta(days=60)).isoformat()}
    check("B", "dedup: credit before the payment isn't matched", ledger.find_duplicate(early, paid, []) is None)
    dom = {**bank, "client_type": "domestic"}   # same amount + timing as a PayPal payout, but an Indian client's UPI
    check("B", "dedup: Indian client's UPI never mistaken for a payout", ledger.find_duplicate(dom, paid, []) is None)

    db.clear()
    base = {"description": "", "currency": "INR", "source": "HDFC Bank", "source_kind": "bank",
            "entry_type": "income", "duplicate_of": None}
    db.add({**base, "date": TODAY.isoformat(), "payer": "Zenith Software", "amount": 90000, "amount_inr": 90000,
            "client_type": "domestic", "tds_inr": 9000})
    db.add({**base, "date": TODAY.isoformat(), "payer": "Priya K", "amount": 5000, "amount_inr": 5000,
            "client_type": "domestic", "needs_review": 1})
    s1 = ledger.summary()
    check("B", "uncertain credit not counted until reviewed",
          s1["by_client_type"]["domestic"] == 90000 and len(s1["needs_review"]) == 1, s1["by_client_type"])
    check("B", "TDS from Indian clients becomes a tax credit", s1["tds_credit_inr"] == 9000, s1["tds_credit_inr"])
    pid = [e for e in db.all_entries() if e["payer"] == "Priya K"][0]["id"]
    db.review_entry(pid, False)
    check("B", "'Not income' keeps it out", ledger.summary()["by_client_type"]["domestic"] == 90000)
    db.review_entry(pid, True)
    check("B", "'Count as income' adds it", ledger.summary()["by_client_type"]["domestic"] == 95000)
    check("B", "INR invoices blocked (PayPal has no domestic INR)",
          any("currency" in e for e in tools.validate("create_and_send_invoice", {"client_name": "Rahul Designs",
              "client_email": "a@rahul.in", "description": "logo", "amount": 38000, "currency": "INR"})[1]))
    db.clear()


# ================================================================ C. RAG retrieval
RAG_CASES = [
    ("Do I need GST registration if all my clients are abroad?", "Do I need GST registration?"),
    ("Is GST charged on invoices to foreign clients?", "Is GST charged on foreign clients?"),
    ("How do I export services without paying IGST?", "LUT: exporting without paying IGST"),
    ("When is advance tax due under the presumptive scheme?", "Due dates if you use the presumptive scheme"),
    ("Do I have to pay advance tax at all?", "Do I have to pay advance tax?"),
    ("What is the receipts limit for 44ADA?", "Receipts limit for professions"),
    ("Is content writing a specified profession?", "Freelancers whose work is a business, not a specified profession"),
    ("Which professions get 50% presumptive?", "Specified professions: 50% of gross receipts"),
    ("What are the new regime tax slabs?", "New regime slabs (default regime)"),
    ("Is income up to 12 lakh tax free?", "Rebate: zero tax up to Rs 12 lakh"),
    ("Can freelancers claim the 75,000 standard deduction?", "Standard deduction"),
    ("Which purpose code for web development?", "Purpose codes"),
    ("What is a FIRC?", "FIRC and FIRA"),
    ("Which ITR form should a freelancer file?", "Which return"),
    ("What records should I keep?", "Records to keep"),
    ("Do Indian clients deduct TDS on my professional fees?", "TDS deducted by Indian business clients"),
    ("How do I treat TDS deducted by my client?", "TDS is your tax already paid"),
    ("Do I charge GST to Indian clients?", "GST for Indian clients"),
    ("Can I send a PayPal invoice to a client in India?", "Why PayPal can't collect from Indian clients"),
    ("Do UPI payments from Indian clients count as income?", "Do payments from Indian clients count?"),
]


def suite_c():
    print("\nC. RAG retrieval")
    for q, expected in RAG_CASES:
        hits = [h["section"] for h in rag.INDEX.search(q, 3)]
        check("C", f"top-3: {q}", expected in hits, hits)
        RESULTS[-1]["top1"] = bool(hits and hits[0] == expected)


# ================================================================ D. Routing (LLM)
ROUTING = [
    ("Bill Acme $1200 for a landing page, ap@acme.com", "billing"),
    ("Create an invoice for Kestrel Health for €900", "billing"),
    ("Show me my invoices", "billing"),
    ("Who hasn't paid me yet?", "collections"),
    ("Chase Nordwind for the late payment", "collections"),
    ("Did any client pay me since yesterday?", "collections"),
    ("How much tax will I owe this year?", "tax"),
    ("Do I need GST registration?", "tax"),
    ("When is my advance tax due?", "tax"),
    ("Should I use 44ADA or the regular method?", "tax"),
    ("Why was the HDFC credit on 31 October not counted?", "statement"),
    ("What did you find in my Upwork statement?", "statement"),
    ("Which bank entries were duplicates?", "statement"),
    ("How much did I earn from Upwork this year?", "tax"),
    ("Bill Rahul Designs in Bangalore ₹38,000 for a logo pack", "billing"),
    ("Is the ₹5,000 UPI from Priya counted as income?", "statement"),
    ("How much TDS have my Indian clients deducted?", "tax"),
    ("Can you give me the split up for the platform fee 99100?", "statement"),
]


def suite_d():
    import graph
    print("\nD. Supervisor routing (LLM)")
    for msg, exp in ROUTING:
        def run():
            out = graph.supervisor({"messages": [{"role": "user", "content": msg}]})
            got = out["route"]
            ok = got == exp or (exp == "tax" and "Upwork" in msg and got == "statement")  # both are acceptable
            check("D", f"'{msg}' → {exp}", ok, f"got {got}")
        safe("D", msg, run)
        time.sleep(1)


# ================================================================ E. Agent behaviour + HITL (LLM)
def _run(msg, autopilot=False):
    import graph
    tid = "eval-" + uuid.uuid4().hex[:8]
    sent_before = len(SENT)
    r = graph.autopilot(tid) if autopilot else graph.run_turn(tid, msg)
    return tid, r, len(SENT) - sent_before


def _tools_used(r):
    return [t.get("tool") for t in r["trace"] if t.get("tool")]


def suite_e():
    import graph
    print("\nE. Agent behaviour + human-in-the-loop (LLM, PayPal mocked)")

    pace = lambda: time.sleep(3)  # stay under the Groq free tier's tokens-per-minute limit

    def e1():
        tid, r, sent = _run("Bill Lumen Studio at lumen@studio.com $600 for a logo design")
        p = r["pending"] or {}
        a = p.get("args", {})
        check("E", "invoice request pauses for approval", p.get("tool") == "create_and_send_invoice", r)
        check("E", "nothing sent before approval", sent == 0, sent)
        check("E", "correct amount + email", float(a.get("amount", 0)) == 600 and a.get("client_email") == "lumen@studio.com", a)
        check("E", "clean client name", a.get("client_name") == "Lumen Studio", a.get("client_name"))
        check("E", "purpose code + LUT on invoice", "P0" in a.get("note", "") and "LUT" in a.get("note", ""), a.get("note"))
        if not r["pending"]:
            return check("E", "edited approval sends once with edited amount", False, "no approval to resume")
        before = len(SENT)
        r2 = graph.resume(tid, {"decision": "edit", "args": {"amount": 650}})
        check("E", "edited approval sends once with edited amount",
              len(SENT) == before + 1 and float(SENT[-1][1]["amount"]) == 650, SENT[-1] if SENT else r2)
    pace(); safe("E", "invoice + approval flow", e1)

    def e2():
        tid, r, sent = _run("Invoice Kestrel Health (kestrel@health.io) EUR 900 for app maintenance, due in 30 days")
        a = (r["pending"] or {}).get("args", {})
        check("E", "picks up currency + due days", a.get("currency") == "EUR" and int(a.get("due_in_days", 0)) == 30, a)
        if not r["pending"]:
            return check("E", "rejected invoice is never sent", False, "no approval to resume")
        before = len(SENT)
        graph.resume(tid, {"decision": "reject", "reason": "wrong client"})
        check("E", "rejected invoice is never sent", len(SENT) == before)
    pace(); safe("E", "currency + reject", e2)

    def e3():
        tid, r, sent = _run("Bill Acme $500 for a website")
        check("E", "missing email → asks instead of guessing",
              not r["pending"] and sent == 0 and "email" in (r["reply"] or "").lower(), r["reply"][:200])
    pace(); safe("E", "missing info", e3)

    def e4():
        tid, r, sent = _run("Send Nordwind a reminder about their overdue invoice")
        p = r["pending"] or {}
        check("E", "reminder pauses for approval with the right invoice",
              p.get("tool") == "send_reminder" and p.get("args", {}).get("invoice_id") == "INV2-OPEN-0002" and sent == 0, p)
    pace(); safe("E", "reminder", e4)

    def e5():
        tid, r, sent = _run("Send a payment reminder for invoice INV2-PAID-0001")
        check("E", "never chases a PAID invoice", not r["pending"] and sent == 0, r["trace"])
    pace(); safe("E", "paid invoice", e5)

    def e6():
        tid, r, sent = _run("Send a reminder for invoice INV2-FAKE-9999")
        check("E", "never acts on an invented invoice ID", not r["pending"] and sent == 0, r["trace"])
    pace(); safe("E", "fake invoice", e6)

    def e7():
        tid, r, sent = _run("How much tax will I owe this year?")
        exp = ledger.summary()["tax"]["estimated_annual_tax_inr"]
        reply = (r["reply"] or "").replace(",", "")
        ok_num = (str(exp) in reply) or (exp == 0 and any(w in reply.lower() for w in ("₹0", "zero", "no tax", "nil")))
        check("E", "tax answer uses the calculator", "get_income_and_tax" in _tools_used(r) and not r["pending"], r["trace"])
        check("E", "tax figure matches the calculator", ok_num, f"expected {exp}; reply: {reply[:200]}")
    pace(); safe("E", "tax", e7)

    def e9():
        tid, r, sent = _run("Bill Rahul Designs Pvt Ltd (accounts@rahuldesigns.in), a client in Bangalore, ₹38,000 for a logo pack")
        reply = (r["reply"] or "").lower()
        check("E", "Indian client: no PayPal invoice attempted", not r["pending"] and sent == 0, r["trace"])
        check("E", "Indian client: explains + offers UPI request", "paypal" in reply and "upi" in reply, reply[:200])
    pace(); safe("E", "indian client", e9)

    def e11():
        tid, r, sent = _run("Who hasn't paid me yet?")
        reply = (r["reply"] or "")
        check("E", "'who hasn't paid' answers with names, no reminder card",
              not r["pending"] and sent == 0 and "Nordwind" in reply, reply[:200])
    pace(); safe("E", "who hasn't paid", e11)

    def e12():
        tid, r, sent = _run("How much tax will I owe this year?")
        reply = (r["reply"] or "").lower()
        jargon = [w for w in ("marginal relief", "cess", "slab") if w in reply]
        check("E", "default tax answer is plain (no jargon)", not jargon, jargon)
    pace(); safe("E", "plain tax", e12)

    def e13():
        tid, r, sent = _run("Can you give me the payment link for Harbor Coffee's invoice?")
        check("E", "gives the client's pay link (no approval needed)",
              not r["pending"] and sent == 0 and "INV2-OPEN-0003" in (r["reply"] or ""), (r["reply"] or "")[:200])
    pace(); safe("E", "pay link", e13)

    def e10():
        tid, r, sent = _run("Show me step by step how my income tax is worked out.")
        reply = (r["reply"] or "").replace(",", "")
        exp = ledger.summary()["tax"]
        check("E", "explains tax using the calculator's working",
              "get_income_and_tax" in _tools_used(r) and str(exp["estimated_annual_tax_inr"]) in reply, reply[:200])
    pace(); safe("E", "tax working", e10)

    def e8():
        tid, r, sent = _run(None, autopilot=True)
        used = _tools_used(r)
        p = r["pending"] or {}
        check("E", "autopilot checks payments + follow-ups",
              "get_payment_updates" in used and "find_invoices_needing_followup" in used, used)
        check("E", "autopilot pauses before emailing a client",
              p.get("tool") == "send_reminder" and p.get("args", {}).get("invoice_id") in ("INV2-OPEN-0002", "INV2-OPEN-0003")
              and sent == 0, p)
    pace(); safe("E", "autopilot", e8)


# ================================================================ F. RAG faithfulness (LLM-as-judge)
FAITH = [
    ("Do I need GST registration if all my clients are abroad?", False),
    ("When do I pay advance tax under the presumptive scheme?", False),
    ("Can I claim the ₹75,000 standard deduction as a freelancer?", False),
    ("Which RBI purpose code should I use for web development work?", False),
    ("Can a content writer use the 50% presumptive scheme?", False),
    ("My Indian client deducted 10% TDS. How should I count that payment?", False),
    ("How are my crypto trading gains taxed in India?", True),   # not in the KB: must abstain
]
JUDGE = """You grade an AI tax assistant. You get the user QUESTION, the knowledge-base PASSAGES it retrieved,
and its ANSWER. Return JSON {"supported": bool, "cited": bool, "abstained": bool, "reason": "..."}.
supported = every factual tax/rule claim in the answer is backed by the passages (numbers from a tax calculator are fine).
cited = the answer cites at least one passage label like [Title › Section] or ends with a Sources line.
abstained = the answer says it doesn't know / the knowledge base doesn't cover it / ask a CA, instead of answering."""


def suite_f():
    import graph
    import llm
    print("\nF. RAG faithfulness (LLM-as-judge)")
    for q, should_abstain in FAITH:
        def run():
            tid, r, _ = _run(q)
            msgs = graph.GRAPH.get_state(graph._cfg(tid)).values["messages"]
            passages = []  # every rule passage retrieved during this turn (the agent may search more than once)
            for m in msgs:
                if m["role"] == "tool" and '"results"' in m["content"]:
                    try:
                        passages += [f"{x['cite']} {x['text']}" for x in json.loads(m["content"])["results"]]
                    except (ValueError, KeyError, TypeError):
                        passages.append(m["content"])
            used_rag = "search_tax_rules" in _tools_used(r)
            verdict = llm.chat_json(JUDGE, json.dumps({"QUESTION": q, "PASSAGES": passages or ["(none)"],
                                                       "ANSWER": r["reply"]})[:12000])
            if should_abstain:
                check("F", f"abstains: {q}", verdict.get("abstained") is True, verdict.get("reason"))
            else:
                check("F", f"retrieves rules: {q}", used_rag, _tools_used(r))
                check("F", f"grounded: {q}", verdict.get("supported") is True, verdict.get("reason"))
                check("F", f"cited: {q}", verdict.get("cited") is True, verdict.get("reason"))
        safe("F", q, run)
        time.sleep(4)


# ================================================================ report
SUITE_NAMES = {"A": "Tax engine correctness", "B": "Guardrails & approval enforcement", "C": "RAG retrieval (top-3)",
               "D": "Supervisor routing", "E": "Agent behaviour + human-in-the-loop", "F": "RAG faithfulness (LLM-as-judge)"}


def report(elapsed):
    lines = ["# Munshi eval scorecard", "",
             f"Run: {time.strftime('%Y-%m-%d %H:%M')} · model `{os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')}` · "
             f"PayPal mocked (nothing sent) · {elapsed:.0f}s" + (" · **offline only**" if OFFLINE else ""), "",
             "| Suite | Passed | Score |", "|---|---|---|"]
    total_p = total_n = 0
    for s, name in SUITE_NAMES.items():
        rows = [r for r in RESULTS if r["suite"] == s]
        if not rows:
            continue
        p = sum(r["passed"] for r in rows)
        total_p, total_n = total_p + p, total_n + len(rows)
        lines.append(f"| {s}. {name} | {p}/{len(rows)} | {100 * p / len(rows):.0f}% |")
    lines.append(f"| **Total** | **{total_p}/{total_n}** | **{100 * total_p / max(1, total_n):.0f}%** |")
    top1 = [r for r in RESULTS if "top1" in r]
    if top1:
        lines += ["", f"RAG top-1 accuracy: {sum(r['top1'] for r in top1)}/{len(top1)}"]
    fails = [r for r in RESULTS if not r["passed"]]
    lines += ["", "## Failures" if fails else "## Failures\n\nNone 🎉"]
    lines += [f"- [{r['suite']}] {r['case']}: {r['detail']}" for r in fails]
    lines += ["", "## All cases", "", "| Suite | Case | Result |", "|---|---|---|"]
    lines += [f"| {r['suite']} | {r['case'].replace('|', '/')} | {'✅' if r['passed'] else '❌'} |" for r in RESULTS]
    open(os.path.join(HERE, "report.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    json.dump(RESULTS, open(os.path.join(HERE, "results.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("\n" + "\n".join(lines[:4 + len(SUITE_NAMES) + 2]))
    print(f"\nFull report: evals/report.md")


if __name__ == "__main__":
    t0 = time.time()
    suite_a(); suite_b(); suite_c()
    if not OFFLINE:
        if not os.getenv("GROQ_API_KEY"):
            sys.exit("GROQ_API_KEY missing in .env (or run with --offline)")
        suite_d(); suite_e(); suite_f()
    report(time.time() - t0)
