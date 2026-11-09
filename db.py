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
    return c


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
