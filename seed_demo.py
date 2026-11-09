"""
Creates demo invoices so the Collections agent (Autopilot) has work to do:
  - one invoice due in 2 days ("due soon")
  - one back-dated invoice that is already overdue (if PayPal sandbox accepts back-dating)
Run once:  python seed_demo.py
"""
import os
from datetime import date, timedelta

from dotenv import load_dotenv

import paypal_client as pp

load_dotenv()
CLIENT = os.getenv("SANDBOX_CLIENT_EMAIL")
NOTE = ("Purpose code P0802. Export of services - supply meant for export under LUT without payment of IGST.")

print(pp.create_and_send_invoice("Harbor Coffee", CLIENT, "Website maintenance - November", 350,
                                 due_in_days=2, note=NOTE))
try:
    past = (date.today() - timedelta(days=25)).isoformat()
    print(pp.create_and_send_invoice("Nordwind GmbH", CLIENT, "React dashboard - 15 hrs", 525,
                                     due_in_days=12, note=NOTE, invoice_date=past))
except pp.PayPalError as e:
    print("PayPal did not accept a back-dated invoice (fine, the 'due soon' one is enough):", e)
