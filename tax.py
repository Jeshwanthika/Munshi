"""
Indian income-tax estimator for freelancers (resident individual, new regime).

Rules used (Tax Year 2026-27, Income Tax Act 2025 - slabs unchanged by Budget 2026):
  Slabs: 0-4L nil | 4-8L 5% | 8-12L 10% | 12-16L 15% | 16-20L 20% | 20-24L 25% | >24L 30%
  Rebate: taxable income up to 12L -> zero tax (with marginal relief just above 12L)
  Cess: 4% health & education cess
  Presumptive scheme for professionals (Sec 58, formerly 44ADA):
      deemed income = 50% of gross receipts, if receipts <= 75L and cash receipts <= 5%
      (PayPal receipts are fully digital, so the 75L limit applies)
  Advance tax under the presumptive scheme: whole amount due by 15 March.
Surcharge (income > 50L) is not modelled. This is an estimate, not tax advice.
"""
from datetime import date

SLABS = [(400_000, 0.0), (800_000, 0.05), (1_200_000, 0.10), (1_600_000, 0.15),
         (2_000_000, 0.20), (2_400_000, 0.25), (float("inf"), 0.30)]
REBATE_LIMIT = 1_200_000
PRESUMPTIVE_LIMIT = 7_500_000
CESS = 0.04


def slab_tax(taxable):
    tax, lower = 0.0, 0
    for upper, rate in SLABS:
        if taxable > lower:
            tax += (min(taxable, upper) - lower) * rate
        lower = upper
    if taxable <= REBATE_LIMIT:
        return 0.0
    # marginal relief: tax can't exceed the income above the rebate limit
    return min(tax, taxable - REBATE_LIMIT)


def total_tax(taxable):
    base = slab_tax(taxable)
    return round(base * (1 + CESS))


def financial_year(today=None):
    today = today or date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


import os

KIND = os.getenv("FREELANCER_TYPE", "profession")   # profession (IT, engineering, consulting...) | business
BUSINESS_RATE, BUSINESS_LIMIT = 0.06, 30_000_000    # Sec 58 business scheme: 6% of digital receipts, 3 crore
GST_THRESHOLD = 2_000_000                            # GST registration: Rs 20 lakh services turnover
ADVANCE_TAX_MIN = 10_000                             # advance tax needed only if tax after TDS >= Rs 10,000


def estimate(gross_receipts_inr, expenses_inr=0.0, today=None, kind=None, tds_inr=0.0):
    """Compare presumptive vs regular (receipts - expenses) for this FY.
    kind='profession': specified profession, 50% of receipts (limit 75 lakh)
    kind='business'  : non-specified work, 6% of digital receipts (limit 3 crore)"""
    today = today or date.today()
    kind = kind or KIND
    rate, limit = (BUSINESS_RATE, BUSINESS_LIMIT) if kind == "business" else (0.5, PRESUMPTIVE_LIMIT)
    fy_start, fy_end = financial_year(today)
    months_elapsed = max(1, (today.year - fy_start.year) * 12 + today.month - fy_start.month + 1)
    projected = gross_receipts_inr * 12 / months_elapsed

    eligible = projected <= limit
    presumptive_income = projected * rate
    regular_income = max(0.0, projected - expenses_inr * 12 / months_elapsed)

    tax_presumptive = total_tax(presumptive_income) if eligible else None
    tax_regular = total_tax(regular_income)
    options = {"regular": tax_regular}
    if eligible:
        options["presumptive"] = tax_presumptive
    # on a tie prefer presumptive: same tax, no books of accounts needed
    best = min(options, key=lambda k: (options[k], k != "presumptive"))
    best_tax = options[best]
    tds_inr = round(tds_inr or 0)
    after_tds = max(0, best_tax - tds_inr)

    return {
        "freelancer_type": kind,
        "presumptive_rate": rate,
        "financial_year": f"{fy_start.year}-{str(fy_end.year)[2:]}",
        "receipts_so_far_inr": round(gross_receipts_inr),
        "projected_annual_receipts_inr": round(projected),
        "presumptive_eligible": eligible,
        "tax_if_presumptive_inr": tax_presumptive,
        "tax_if_regular_inr": tax_regular,
        "recommended": best,
        "estimated_annual_tax_inr": best_tax,
        "tds_credit_inr": tds_inr,
        "tax_after_tds_inr": after_tds,
        "refund_due_inr": max(0, tds_inr - best_tax),
        "set_aside_percent_of_each_payment": round(100 * after_tds / projected, 1) if projected else 0,
        "advance_tax_deadline": ("No advance tax needed (tax after TDS is under Rs 10,000)" if after_tds < ADVANCE_TAX_MIN else
                                 f"Pay full advance tax by 15 March {fy_end.year} (presumptive scheme)"
                                 if best == "presumptive" else
                                 "Quarterly: 15% by 15 Jun, 45% by 15 Sep, 75% by 15 Dec, 100% by 15 Mar"),
        "assumptions": [
            "Resident individual, under 60, new tax regime (default) for Tax Year 2026-27",
            "Projection = receipts so far scaled to 12 months of the financial year",
            (f"Presumptive: {rate:.0%} of receipts deemed income (Sec 58; "
             + ("specified profession, formerly 44ADA" if kind != "business" else "business scheme for digital receipts, formerly 44AD")
             + "); all PayPal receipts are digital"),
            "Regular method: receipts minus recorded platform fees / expenses you provide",
            "No other income (salary, interest, capital gains) and no surcharge (income above 50 lakh)",
            "Foreign currency converted at a single configured rate, not the rate on each receipt date",
            "TDS credit = TDS shown in imported statements so far; check it against Form 26AS / AIS",
        ],
        "disclaimer": "Estimate only. Confirm with a CA before filing.",
    }


if __name__ == "__main__":
    # quick self-check
    assert total_tax(1_200_000) == 0
    assert total_tax(1_600_000) == round(120_000 * 1.04)
    print(estimate(1_100_000, 150_000, today=date(2026, 11, 9)))
