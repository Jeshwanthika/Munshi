"""
Munshi - Step 1 connection test.

Checks that your PayPal sandbox keys work by:
  1. Getting an access token
  2. Creating a draft invoice to your sandbox "client"
  3. Sending it (so you get a payment link)

Run:  python test_paypal.py
"""
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = "https://api-m.sandbox.paypal.com"
CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
CLIENT_EMAIL = os.getenv("SANDBOX_CLIENT_EMAIL")  # the sandbox *Personal* account email

if not (CLIENT_ID and CLIENT_SECRET and CLIENT_EMAIL):
    sys.exit("Missing values in .env - fill PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, SANDBOX_CLIENT_EMAIL")


def get_token():
    r = requests.post(
        f"{BASE}/v1/oauth2/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"Could not log in to PayPal ({r.status_code}): {r.text}")
    return r.json()["access_token"]


def main():
    token = get_token()
    print("1/3  Connected to PayPal sandbox")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    invoice = {
        "detail": {
            "currency_code": "USD",
            "note": "Landing page design - thank you for your business!",
            "payment_term": {"term_type": "NET_15"},
        },
        "primary_recipients": [
            {"billing_info": {"name": {"given_name": "Acme", "surname": "Corp"},
                              "email_address": CLIENT_EMAIL}}
        ],
        "items": [
            {"name": "Landing page design", "quantity": "1",
             "unit_amount": {"currency_code": "USD", "value": "1200.00"},
             "unit_of_measure": "AMOUNT"}
        ],
    }
    r = requests.post(f"{BASE}/v2/invoicing/invoices", json=invoice, headers=headers, timeout=30)
    if r.status_code not in (200, 201):
        sys.exit(f"Invoice creation failed ({r.status_code}): {r.text}")
    inv = r.json()
    inv_id = inv.get("id") or inv.get("href", "").rstrip("/").split("/")[-1]
    print(f"2/3  Draft invoice created: {inv_id}")

    r = requests.post(f"{BASE}/v2/invoicing/invoices/{inv_id}/send",
                      json={"send_to_invoicer": True}, headers=headers, timeout=30)
    if r.status_code not in (200, 202):
        sys.exit(f"Sending failed ({r.status_code}): {r.text}")
    link = r.json().get("href", "(check your sandbox dashboard)")
    print("3/3  Invoice sent!")
    print(f"\nPayment link for your client:\n{link}")
    print("\nOpen it, log in with the sandbox PERSONAL account, and pay. That's your first AI-free win.")


if __name__ == "__main__":
    main()
