"""Checks the Munshi MCP server works like a real MCP client would use it.  Run:  python mcp_selftest.py"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))


async def main():
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "mcp_server.py")])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            print("TOOLS:", [t.name for t in tools])
            res = (await s.list_resources()).resources
            print("RESOURCES:", [str(r.uri) for r in res])
            print("PROMPTS:", [p.name for p in (await s.list_prompts()).prompts])

            r = await s.call_tool("search_tax_rules", {"query": "Do I need GST registration?"})
            print("\nsearch_tax_rules ->", r.content[0].text[:300], "...")
            r = await s.call_tool("draft_invoice", {"client_name": "Bill Acme", "client_email": "a@acme.com",
                                                    "description": "Landing page", "amount": 1200})
            print("\ndraft_invoice ->", r.content[0].text[:400])
            try:
                r = await s.call_tool("get_income_and_tax", {})
                tax = json.loads(r.content[0].text).get("tax", {})
                print("\nget_income_and_tax -> estimated tax:", tax.get("estimated_annual_tax_inr"))
            except Exception as e:  # needs PayPal keys in .env
                print("\nget_income_and_tax -> (needs PayPal keys)", e)
            print("\nSend-capable tools exposed:", [t.name for t in tools if "send" in t.name] or "none ✓")


asyncio.run(main())
