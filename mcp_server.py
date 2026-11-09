"""
Munshi MCP server: use Munshi's books, tax engine and tax-rules RAG from any MCP client
(Claude Desktop, Cursor, VS Code, or another agent).

Safety by design: this server exposes READ-ONLY tools plus a side-effect-free invoice draft.
Sending invoices or reminders is never possible over MCP. Those actions need a human approval
inside Munshi, and the tool layer (tools.run_tool) enforces that for every caller.

Run (stdio):  python mcp_server.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)                 # MCP clients may start us from any folder: find .env and munshi.db here
sys.path.insert(0, HERE)

try:                                    # MCP Python SDK v2
    from mcp.server.mcpserver import MCPServer as Server  # noqa: E402
except ImportError:                     # SDK v1
    from mcp.server.fastmcp import FastMCP as Server  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

import rag  # noqa: E402
import tools  # noqa: E402

mcp = Server("munshi", instructions=(
    "Munshi is an AI accountant for Indian freelancers paid by foreign clients via PayPal. "
    "Use these tools to read PayPal invoices, income across PayPal/Upwork/bank (duplicates removed), "
    "Indian income-tax estimates from a deterministic calculator, and cited tax rules. "
    "Never compute tax figures yourself: use get_income_and_tax. Sending anything to clients is not "
    "available here; the user approves that inside the Munshi app."))


READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@mcp.tool(annotations=READ_ONLY)
def get_income_and_tax(extra_expenses_inr: float = 0) -> dict:
    """This financial year's income in INR by source (PayPal + imported statements, double counting removed),
    pending and overdue invoices, and the income-tax estimate (presumptive vs regular, set-aside %, deadline,
    assumptions)."""
    return tools.run_tool("get_income_and_tax", {"extra_expenses_inr": extra_expenses_inr or None})


@mcp.tool(annotations=READ_ONLY)
def list_invoices() -> list | dict:
    """All PayPal invoices with client, amount, status (SENT, UNPAID, PAID...) and due date."""
    return tools.run_tool("list_invoices", {})


@mcp.tool(annotations=READ_ONLY)
def find_invoices_needing_followup() -> list | dict:
    """Unpaid PayPal invoices that are overdue or due within 3 days, with days overdue."""
    return tools.run_tool("find_invoices_needing_followup", {})


@mcp.tool(annotations=READ_ONLY)
def search_tax_rules(query: str) -> dict:
    """Search Munshi's Indian tax & compliance knowledge base (Section 58 presumptive scheme, advance tax,
    slabs & rebate, GST on exports & LUT, RBI purpose codes & FIRC, ITR). Returns passages with citations."""
    return tools.run_tool("search_tax_rules", {"query": query})


@mcp.tool(annotations=READ_ONLY)
def list_imported_entries() -> list | dict:
    """Entries imported from Upwork/bank statements, incl. bank credits ignored as duplicates and why."""
    return tools.run_tool("list_imported_entries", {})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, title="Draft invoice (not sent)"))
def draft_invoice(client_name: str, client_email: str, description: str, amount: float,
                  currency: str = "USD", due_in_days: int = 15, note: str = "") -> dict:
    """Validate and prepare a PayPal invoice WITHOUT sending it: fixes the client name, adds the RBI purpose
    code and LUT export line, and reports problems. To send it, the user approves it in the Munshi app."""
    args, errors = tools.validate("create_and_send_invoice", {
        "client_name": client_name, "client_email": client_email, "description": description,
        "amount": amount, "currency": currency, "due_in_days": due_in_days, "note": note})
    return {"draft": args, "valid": not errors, "errors": errors,
            "next_step": "Open Munshi and say: " + (f"Bill {args['client_name']} at {args['client_email']} "
                                                      f"{args['currency']} {args['amount']} for {args['description']}")}


# Knowledge base documents as MCP resources (clients can attach them as context)
for _fn in sorted(os.listdir(rag.KB_DIR)):
    if _fn.endswith(".md"):
        def _make(fn):
            def read() -> str:
                return open(os.path.join(rag.KB_DIR, fn), encoding="utf-8").read()
            read.__name__ = "kb_" + fn[:-3]
            read.__doc__ = open(os.path.join(rag.KB_DIR, fn), encoding="utf-8").readline().lstrip("# ").strip()
            return read
        mcp.resource(f"munshi://knowledge/{_fn[:-3]}", mime_type="text/markdown")(_make(_fn))


@mcp.prompt()
def tax_checkup() -> str:
    """A ready-made prompt: review my income and tax position."""
    return ("Use get_income_and_tax and search_tax_rules to give me a short tax check-up: income so far, "
            "estimated tax, how much to set aside, the next deadline, and whether I may need GST registration. "
            "Cite the rules you use.")


if __name__ == "__main__":
    mcp.run()
