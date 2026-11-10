"""Set Munshi's login username + password (stored as a scrypt hash in .env).  Run:  python set_password.py"""
import getpass
import os
import re
import secrets

import auth

ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def upsert(text, key, value):
    line = f"{key}={value}"
    return re.sub(rf"^{key}=.*$", line, text, flags=re.M) if re.search(rf"^{key}=", text, re.M) else text.rstrip() + "\n" + line + "\n"


user = input("Username [admin]: ").strip() or "admin"
while True:
    pw = getpass.getpass("New password (min 8 chars, typing is hidden): ")
    if len(pw) >= 8 and pw == getpass.getpass("Repeat password: "):
        break
    print("Passwords didn't match or were too short. Try again.")

text = open(ENV, encoding="utf-8").read() if os.path.exists(ENV) else ""
text = upsert(text, "MUNSHI_USERNAME", user)
text = upsert(text, "MUNSHI_PASSWORD_HASH", auth.hash_password(pw))
if not re.search(r"^MUNSHI_SECRET_KEY=.+$", text, re.M):
    text = upsert(text, "MUNSHI_SECRET_KEY", secrets.token_hex(32))
open(ENV, "w", encoding="utf-8").write(text)
print(f"Saved. Username '{user}' can now log in. Restart the server (Ctrl+C, then start again).")
