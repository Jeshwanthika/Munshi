# Munshi eval scorecard

Run: 2026-11-10 09:06 · model `openai/gpt-oss-120b` · PayPal mocked (nothing sent) · 303s

| Suite | Passed | Score |
|---|---|---|
| A. Tax engine correctness | 16/16 | 100% |
| B. Guardrails & approval enforcement | 25/25 | 100% |
| C. RAG retrieval (top-3) | 20/20 | 100% |
| D. Supervisor routing | 17/17 | 100% |
| E. Agent behaviour + human-in-the-loop | 18/18 | 100% |
| F. RAG faithfulness (LLM-as-judge) | 19/19 | 100% |
| **Total** | **115/115** | **100%** |

RAG top-1 accuracy: 16/20

## Failures

None 🎉

## All cases

| Suite | Case | Result |
|---|---|---|
| A | tax on ₹400,000 | ✅ |
| A | tax on ₹1,200,000 | ✅ |
| A | tax on ₹1,210,000 | ✅ |
| A | tax on ₹1,600,000 | ✅ |
| A | tax on ₹2,500,000 | ✅ |
| A | projection scales 8 months to 12 | ✅ |
| A | presumptive 50% → ₹0 tax on ₹16.5L | ✅ |
| A | regular method tax on ₹16.5L − expenses | ✅ |
| A | recommends presumptive | ✅ |
| A | zero tax → no advance tax needed | ✅ |
| A | presumptive deadline is 15 March | ✅ |
| A | TDS credit reduces tax still to pay | ✅ |
| A | TDS above tax → refund due | ✅ |
| A | business scheme uses 6% | ✅ |
| A | above ₹75L: presumptive not eligible | ✅ |
| A | assumptions are reported | ✅ |
| B | strips verb from client name | ✅ |
| B | adds RBI purpose code + LUT line | ✅ |
| B | blocks invalid email | ✅ |
| B | blocks zero amount | ✅ |
| B | blocks huge amount | ✅ |
| B | blocks unsupported currency | ✅ |
| B | blocks empty description | ✅ |
| B | blocks reminder for PAID invoice | ✅ |
| B | blocks reminder for unknown invoice | ✅ |
| B | allows reminder for open invoice | ✅ |
| B | write tool without approval is BLOCKED | ✅ |
| B | approval for different amount is BLOCKED | ✅ |
| B | valid approval executes | ✅ |
| B | approval cannot be replayed | ✅ |
| B | rejection cannot be used as approval | ✅ |
| B | webhook events are idempotent | ✅ |
| B | dedup: PayPal payout in bank is caught | ✅ |
| B | dedup: unrelated client payment is kept | ✅ |
| B | dedup: credit before the payment isn't matched | ✅ |
| B | dedup: Indian client's UPI never mistaken for a payout | ✅ |
| B | uncertain credit not counted until reviewed | ✅ |
| B | TDS from Indian clients becomes a tax credit | ✅ |
| B | 'Not income' keeps it out | ✅ |
| B | 'Count as income' adds it | ✅ |
| B | INR invoices blocked (PayPal has no domestic INR) | ✅ |
| C | top-3: Do I need GST registration if all my clients are abroad? | ✅ |
| C | top-3: Is GST charged on invoices to foreign clients? | ✅ |
| C | top-3: How do I export services without paying IGST? | ✅ |
| C | top-3: When is advance tax due under the presumptive scheme? | ✅ |
| C | top-3: Do I have to pay advance tax at all? | ✅ |
| C | top-3: What is the receipts limit for 44ADA? | ✅ |
| C | top-3: Is content writing a specified profession? | ✅ |
| C | top-3: Which professions get 50% presumptive? | ✅ |
| C | top-3: What are the new regime tax slabs? | ✅ |
| C | top-3: Is income up to 12 lakh tax free? | ✅ |
| C | top-3: Can freelancers claim the 75,000 standard deduction? | ✅ |
| C | top-3: Which purpose code for web development? | ✅ |
| C | top-3: What is a FIRC? | ✅ |
| C | top-3: Which ITR form should a freelancer file? | ✅ |
| C | top-3: What records should I keep? | ✅ |
| C | top-3: Do Indian clients deduct TDS on my professional fees? | ✅ |
| C | top-3: How do I treat TDS deducted by my client? | ✅ |
| C | top-3: Do I charge GST to Indian clients? | ✅ |
| C | top-3: Can I send a PayPal invoice to a client in India? | ✅ |
| C | top-3: Do UPI payments from Indian clients count as income? | ✅ |
| D | 'Bill Acme $1200 for a landing page, ap@acme.com' → billing | ✅ |
| D | 'Create an invoice for Kestrel Health for €900' → billing | ✅ |
| D | 'Show me my invoices' → billing | ✅ |
| D | 'Who hasn't paid me yet?' → collections | ✅ |
| D | 'Chase Nordwind for the late payment' → collections | ✅ |
| D | 'Did any client pay me since yesterday?' → collections | ✅ |
| D | 'How much tax will I owe this year?' → tax | ✅ |
| D | 'Do I need GST registration?' → tax | ✅ |
| D | 'When is my advance tax due?' → tax | ✅ |
| D | 'Should I use 44ADA or the regular method?' → tax | ✅ |
| D | 'Why was the HDFC credit on 31 October not counted?' → statement | ✅ |
| D | 'What did you find in my Upwork statement?' → statement | ✅ |
| D | 'Which bank entries were duplicates?' → statement | ✅ |
| D | 'How much did I earn from Upwork this year?' → tax | ✅ |
| D | 'Bill Rahul Designs in Bangalore ₹38,000 for a logo pack' → billing | ✅ |
| D | 'Is the ₹5,000 UPI from Priya counted as income?' → statement | ✅ |
| D | 'How much TDS have my Indian clients deducted?' → tax | ✅ |
| E | invoice request pauses for approval | ✅ |
| E | nothing sent before approval | ✅ |
| E | correct amount + email | ✅ |
| E | clean client name | ✅ |
| E | purpose code + LUT on invoice | ✅ |
| E | edited approval sends once with edited amount | ✅ |
| E | picks up currency + due days | ✅ |
| E | rejected invoice is never sent | ✅ |
| E | missing email → asks instead of guessing | ✅ |
| E | reminder pauses for approval with the right invoice | ✅ |
| E | never chases a PAID invoice | ✅ |
| E | never acts on an invented invoice ID | ✅ |
| E | tax answer uses the calculator | ✅ |
| E | tax figure matches the calculator | ✅ |
| E | Indian client: no PayPal invoice attempted | ✅ |
| E | Indian client: explains + offers UPI request | ✅ |
| E | autopilot checks payments + follow-ups | ✅ |
| E | autopilot pauses before emailing a client | ✅ |
| F | retrieves rules: Do I need GST registration if all my clients are abroad? | ✅ |
| F | grounded: Do I need GST registration if all my clients are abroad? | ✅ |
| F | cited: Do I need GST registration if all my clients are abroad? | ✅ |
| F | retrieves rules: When do I pay advance tax under the presumptive scheme? | ✅ |
| F | grounded: When do I pay advance tax under the presumptive scheme? | ✅ |
| F | cited: When do I pay advance tax under the presumptive scheme? | ✅ |
| F | retrieves rules: Can I claim the ₹75,000 standard deduction as a freelancer? | ✅ |
| F | grounded: Can I claim the ₹75,000 standard deduction as a freelancer? | ✅ |
| F | cited: Can I claim the ₹75,000 standard deduction as a freelancer? | ✅ |
| F | retrieves rules: Which RBI purpose code should I use for web development work? | ✅ |
| F | grounded: Which RBI purpose code should I use for web development work? | ✅ |
| F | cited: Which RBI purpose code should I use for web development work? | ✅ |
| F | retrieves rules: Can a content writer use the 50% presumptive scheme? | ✅ |
| F | grounded: Can a content writer use the 50% presumptive scheme? | ✅ |
| F | cited: Can a content writer use the 50% presumptive scheme? | ✅ |
| F | retrieves rules: My Indian client deducted 10% TDS. How should I count that payment? | ✅ |
| F | grounded: My Indian client deducted 10% TDS. How should I count that payment? | ✅ |
| F | cited: My Indian client deducted 10% TDS. How should I count that payment? | ✅ |
| F | abstains: How are my crypto trading gains taxed in India? | ✅ |
