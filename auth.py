"""
Login for Munshi (single owner account).

- Password stored only as a scrypt hash (MUNSHI_PASSWORD_HASH in .env), created with:  python set_password.py
- Session = HMAC-signed cookie (HttpOnly, SameSite=Lax, 12 h), signed with MUNSHI_SECRET_KEY
- Brute-force protection: 5 failed logins per IP -> 10 minute lockout
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from dotenv import load_dotenv

load_dotenv()

COOKIE = "munshi_session"
SESSION_SECONDS = 12 * 3600
USERNAME = os.getenv("MUNSHI_USERNAME", "admin")
SECRET = (os.getenv("MUNSHI_SECRET_KEY") or secrets.token_hex(32)).encode()  # random = logins reset on restart
_FAILS = {}
MAX_FAILS, LOCK_SECONDS = 5, 600


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def configured():
    return bool(os.getenv("MUNSHI_PASSWORD_HASH"))


def verify_password(password):
    stored = os.getenv("MUNSHI_PASSWORD_HASH", "")
    try:
        _, salt_b64, _ = stored.split("$")
        expected = hash_password(password, base64.b64decode(salt_b64))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, stored)


def _sign(data):
    return base64.urlsafe_b64encode(hmac.new(SECRET, data, hashlib.sha256).digest()).decode().rstrip("=")


def make_session(user):
    payload = base64.urlsafe_b64encode(json.dumps({"u": user, "exp": int(time.time()) + SESSION_SECONDS}).encode())
    return payload.decode() + "." + _sign(payload)


def read_session(token):
    """Returns the username if the cookie is genuine and not expired, else None."""
    try:
        payload, sig = (token or "").rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(payload.encode())):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return data["u"] if data["exp"] > time.time() else None
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def locked(ip):
    fails = [t for t in _FAILS.get(ip, []) if time.time() - t < LOCK_SECONDS]
    _FAILS[ip] = fails
    return len(fails) >= MAX_FAILS


def record_failure(ip):
    _FAILS.setdefault(ip, []).append(time.time())


def check_login(ip, username, password):
    """Returns (ok, message)."""
    if not configured():
        return False, "Login isn't set up yet: run 'python set_password.py' and restart the server."
    if locked(ip):
        return False, "Too many attempts. Try again in 10 minutes."
    ok = hmac.compare_digest((username or "").strip().lower(), USERNAME.lower()) & verify_password(password or "")
    if not ok:
        record_failure(ip)
        return False, "Wrong username or password."
    _FAILS.pop(ip, None)
    return True, "ok"
