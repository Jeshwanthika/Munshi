"""Prints a short-lived PayPal sandbox access token (valid ~9 hours) for PayPal's official MCP server.
Run:  python paypal_token.py      Never share or commit the token."""
import paypal_client as pp

print(pp._get_token())
