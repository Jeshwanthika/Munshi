"""Tiny SQLite store for income Munshi imports from non-PayPal sources (Upwork, bank, etc.)."""
import os
import sqlite3

DB_PATH = os.getenv("MUNSHI_DB", "munshi.db")


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, payer TEXT, description TEXT,
        amount REAL, currency TEXT, amount_inr REAL,
        source TEXT, source_kind TEXT, entry_type TEXT,
        duplicate_of TEXT,
        UNIQUE(date, amount, currency, source, entry_type))""")
    c.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        tool TEXT, args_hash TEXT, args TEXT, decision TEXT, used INTEGER DEFAULT 0, result TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS webhook_events (
        event_id TEXT PRIMARY KEY, received_at TEXT DEFAULT CURRENT_TIMESTAMP,
        event_type TEXT, resource_id TEXT, payload TEXT)""")
    return c


def record_approval(tool, args_hash, args_json, decision):
    with conn() as c:
        return c.execute("INSERT INTO approvals (tool, args_hash, args, decision) VALUES (?,?,?,?)",
                         (tool, args_hash, args_json, decision)).lastrowid


def consume_approval(approval_id, tool, args_hash):
    """True exactly once for an approved, unused record matching this tool + exact arguments."""
    with conn() as c:
        cur = c.execute("""UPDATE approvals SET used=1 WHERE id=? AND tool=? AND args_hash=?
                           AND decision IN ('approve','edit') AND used=0""", (approval_id, tool, args_hash))
        return cur.rowcount == 1


def set_approval_result(approval_id, result):
    with conn() as c:
        c.execute("UPDATE approvals SET result=? WHERE id=?", (result, approval_id))


def audit_log(limit=50):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (limit,))]


def save_webhook_event(event_id, event_type, resource_id, payload):
    """Idempotency: returns False if this PayPal event was already processed."""
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO webhook_events (event_id, event_type, resource_id, payload) "
                        "VALUES (?,?,?,?)", (event_id, event_type, resource_id, payload))
        return cur.rowcount == 1


def get_kv(key, default=None):
    with conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_kv(key, value):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))


def add(entry):
    with conn() as c:
        cur = c.execute("""INSERT OR IGNORE INTO entries
            (date, payer, description, amount, currency, amount_inr, source, source_kind, entry_type, duplicate_of)
            VALUES (:date, :payer, :description, :amount, :currency, :amount_inr, :source, :source_kind,
                    :entry_type, :duplicate_of)""", entry)
        return cur.rowcount == 1


def all_entries():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM entries ORDER BY date DESC")]


def clear():
    with conn() as c:
        c.execute("DELETE FROM entries")
