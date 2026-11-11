"""Thin wrapper around the PayPal REST APIs Munshi uses (sandbox by default)."""
import os
import time
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
WEB = "https://www.sandbox.paypal.com" if "sandbox" in BASE else "https://www.paypal.com"

_token = {"value": None, "expires": 0}


class PayPalError(Exception):
    pass


def _get_token():
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    r = requests.post(
        f"{BASE}/v1/oauth2/token",
        auth=(os.getenv("PAYPAL_CLIENT_ID"), os.getenv("PAYPAL_CLIENT_SECRET")),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    if r.status_code == 401:
        raise PayPalError("PayPal rejected your app keys (401 invalid_client). Copy the current Client ID and "
                          "Secret from developer.paypal.com into .env, then fully restart the server (Ctrl+C).")
    if r.status_code != 200:
        raise PayPalError(f"PayPal login failed ({r.status_code}): {r.text}")
    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + int(data.get("expires_in", 3000))
    return _token["value"]


RETRYABLE = {429, 500, 502, 503, 504}


def _call(method, path, **kwargs):
    """Reads (GET) are retried on PayPal hiccups (5xx/429/network) with backoff 1s, 2s, 4s.
    Writes are NOT retried automatically, so a flaky network can never create a duplicate invoice."""
    attempts = 4 if method == "GET" else 1
    for i in range(attempts):
        headers = {
            "Authorization": f"Bearer {_get_token()}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        try:
            r = requests.request(method, f"{BASE}{path}", headers=headers, timeout=30, **kwargs)
        except requests.RequestException as e:
            if i < attempts - 1:
                time.sleep(2 ** i)
                continue
            raise PayPalError(f"PayPal is not reachable right now ({e.__class__.__name__}). Please try again.")
        if r.status_code in RETRYABLE and i < attempts - 1:
            time.sleep(2 ** i)
            continue
        if r.status_code in RETRYABLE:
            raise PayPalError(f"PayPal sandbox is temporarily unavailable ({r.status_code}). Please try again shortly.")
        if r.status_code >= 400:
            raise PayPalError(f"PayPal {method} {path} failed ({r.status_code}): {r.text[:300]}")
        return r.json() if r.text else {}


def payer_link(invoice_id):
    return f"{WEB}/invoice/p/#{invoice_id}"


# ---------- Invoices ----------

def create_and_send_invoice(client_name, client_email, description, amount,
                            currency="USD", due_in_days=15, note="", invoice_date=None):
    start = date.fromisoformat(invoice_date) if invoice_date else date.today()
    due = (start + timedelta(days=int(due_in_days))).isoformat()
    parts = client_name.strip().split(" ", 1)
    body = {
        "detail": {
            "currency_code": currency,
            "note": note or "Thank you for your business!",
            "payment_term": {"due_date": due},
            **({"invoice_date": invoice_date} if invoice_date else {}),
        },
        "primary_recipients": [{
            "billing_info": {
                "name": {"given_name": parts[0], "surname": parts[1] if len(parts) > 1 else ""},
                "email_address": client_email,
            }
        }],
        "items": [{
            "name": description[:200],
            "quantity": "1",
            "unit_amount": {"currency_code": currency, "value": f"{float(amount):.2f}"},
            "unit_of_measure": "AMOUNT",
        }],
    }
    inv = _call("POST", "/v2/invoicing/invoices", json=body)
    inv_id = inv.get("id") or inv.get("href", "").rstrip("/").split("/")[-1]
    _call("POST", f"/v2/invoicing/invoices/{inv_id}/send", json={"send_to_invoicer": True})
    return {"invoice_id": inv_id, "amount": f"{currency} {float(amount):.2f}",
            "due_date": due, "client": client_name, "pay_link": payer_link(inv_id)}


SYNC = {"ok": None, "at": None, "error": None}   # status of the last PayPal sync, shown on the dashboard
_CACHE = {"invoices": None}


def list_invoices(page_size=50):
    """Live invoices from PayPal. If PayPal is down, fall back to the last successful sync (flagged as stale)."""
    try:
        data = _call("GET", f"/v2/invoicing/invoices?page_size={page_size}&total_required=true")
    except PayPalError as e:
        SYNC.update(ok=False, error=str(e))
        if _CACHE["invoices"] is not None:
            return [dict(i) for i in _CACHE["invoices"]]
        raise
    out = []
    for inv in data.get("items", []):
        rec = (inv.get("primary_recipients") or [{}])[0].get("billing_info", {})
        name = rec.get("name", {})
        amt = inv.get("amount", {})
        detail = inv.get("detail", {})
        out.append({
            "invoice_id": inv.get("id"),
            "status": inv.get("status"),
            "client": f"{name.get('given_name', '')} {name.get('surname', '')}".strip(),
            "client_email": rec.get("email_address"),
            "currency": amt.get("currency_code"),
            "amount": amt.get("value"),
            "invoice_date": detail.get("invoice_date"),
            "due_date": (detail.get("payment_term") or {}).get("due_date"),
            # where the client pays: PayPal's own recipient link if given, else the standard invoice link
            "pay_link": ((detail.get("metadata") or {}).get("recipient_view_url") or payer_link(inv.get("id")))
                        if inv.get("status") in ("SENT", "UNPAID", "PARTIALLY_PAID", "SCHEDULED") else None,
        })
    _CACHE["invoices"] = out
    SYNC.update(ok=True, at=time.strftime("%H:%M:%S"), error=None)
    return out


def send_reminder(invoice_id, note):
    _call("POST", f"/v2/invoicing/invoices/{invoice_id}/remind",
          json={"subject": "Friendly reminder: invoice due", "note": note,
                "send_to_invoicer": True})
    return {"invoice_id": invoice_id, "reminder_sent": True}


# ---------- Webhooks ----------

def verify_webhook(headers, event):
    """Ask PayPal to verify the webhook signature. Never trust an unverified event."""
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID")
    if not webhook_id:
        return False
    h = {k.lower(): v for k, v in headers.items()}
    body = {
        "auth_algo": h.get("paypal-auth-algo"),
        "cert_url": h.get("paypal-cert-url"),
        "transmission_id": h.get("paypal-transmission-id"),
        "transmission_sig": h.get("paypal-transmission-sig"),
        "transmission_time": h.get("paypal-transmission-time"),
        "webhook_id": webhook_id,
        "webhook_event": event,
    }
    if not all(body.values()):
        return False
    res = _call("POST", "/v1/notifications/verify-webhook-signature", json=body)
    return res.get("verification_status") == "SUCCESS"
