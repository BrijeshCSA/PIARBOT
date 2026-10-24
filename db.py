# -*- coding: utf-8 -*-
import os
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

log = logging.getLogger("db")

# Сохраняем БД в /app/data (требование BotHost)
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "piar.db"))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

ROLE_PRIORITY = {"owner": 4, "deputy": 3, "admin": 2, "moderator": 1}

_conn: Optional[sqlite3.Connection] = None

def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_schema(_conn)
        log.info(f"DB ready: {os.path.abspath(DB_PATH)}")
    return _conn

def _init_schema(conn):
    if SCHEMA_PATH.exists():
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
    else:
        conn.executescript(_INLINE)
    conn.commit()

def execute(sql, params=()):
    c = get_conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur

def fetchone(sql, params=()):
    return get_conn().execute(sql, params).fetchone()

def fetchall(sql, params=()):
    return get_conn().execute(sql, params).fetchall()

def ensure_user(user_id, username=None, first_name=None):
    now = datetime.now().isoformat()
    execute(
        """INSERT INTO users (user_id, username, first_name, last_seen)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             username   = COALESCE(excluded.username, users.username),
             first_name = COALESCE(excluded.first_name, users.first_name),
             last_seen  = excluded.last_seen""",
        (user_id, username, first_name, now),
    )

def get_user(user_id):
    return fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

def get_balance(user_id) -> int:
    r = fetchone("SELECT balance FROM users WHERE user_id=?", (user_id,))
    return r["balance"] if r else 0

def add_balance(user_id, amount) -> int:
    ensure_user(user_id)
    execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    return get_balance(user_id)

def deduct_balance(user_id, amount) -> bool:
    if get_balance(user_id) < amount: return False
    execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
    return True

def set_subscribed(user_id) -> bool:
    ensure_user(user_id)
    r = fetchone("SELECT subscribed FROM users WHERE user_id=?", (user_id,))
    if r and r["subscribed"]: return False
    execute("UPDATE users SET subscribed=1, balance = balance + 100 WHERE user_id=?", (user_id,))
    return True

def add_chat(chat_id, title=""):
    execute("INSERT OR REPLACE INTO chats (chat_id, title) VALUES (?, ?)", (chat_id, title or ""))

def list_chats():
    return [r["chat_id"] for r in fetchall("SELECT chat_id FROM chats")]

def create_order(user_id, interval_min, price) -> int:
    ensure_user(user_id)
    cur = execute("INSERT INTO orders (user_id, interval_min, price) VALUES (?, ?, ?)", (user_id, interval_min, price))
    return cur.lastrowid

def get_order(order_id):
    return fetchone("SELECT * FROM orders WHERE id=?", (order_id,))

def fund_order(order_id, confirmed_by):
    order = get_order(order_id)
    if not order or order["status"] != "pending": return None
    execute("UPDATE orders SET status='funded', confirmed_by=?, funded_at=? WHERE id=?", (confirmed_by, datetime.now().isoformat(), order_id))
    add_balance(order["user_id"], order["price"])
    execute("UPDATE users SET total_paid = total_paid + ? WHERE user_id=?", (order["price"], order["user_id"]))
    return order

def create_ad(user_id, text, interval_min, price) -> int:
    ensure_user(user_id)
    cur = execute("INSERT INTO ads (user_id, text, interval_min, price) VALUES (?, ?, ?, ?)", (user_id, text, interval_min, price))
    return cur.lastrowid

def list_user_ads(user_id):
    return fetchall("SELECT * FROM ads WHERE user_id=? ORDER BY id DESC", (user_id,))

def list_active_ads():
    return fetchall("SELECT * FROM ads WHERE status='active' ORDER BY id DESC")

def mark_ad_sent(ad_id):
    execute("UPDATE ads SET last_sent=?, sent_count = sent_count + 1 WHERE id=?", (datetime.now().isoformat(), ad_id))

def grant_role(user_id, role, granted_by):
    execute("INSERT OR IGNORE INTO roles (user_id, role, granted_by) VALUES (?, ?, ?)", (user_id, role, granted_by))

def get_roles(user_id):
    return [r["role"] for r in fetchall("SELECT role FROM roles WHERE user_id=?", (user_id,))]

def has_min_role(user_id, min_role, owner_id=0) -> bool:
    if user_id == owner_id: return True
    need = ROLE_PRIORITY.get(min_role, 0)
    return any(ROLE_PRIORITY.get(r, 0) >= need for r in get_roles(user_id))

def stats() -> Dict[str, Any]:
    return {
        "users": fetchone("SELECT COUNT(*) c FROM users")["c"],
        "chats": fetchone("SELECT COUNT(*) c FROM chats")["c"],
        "active": fetchone("SELECT COUNT(*) c FROM ads WHERE status='active'")["c"],
        "orders": fetchone("SELECT COUNT(*) c FROM orders")["c"],
        "funded": fetchone("SELECT COALESCE(SUM(price),0) s FROM orders WHERE status='funded'")["s"],
    }

_INLINE = """
CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER DEFAULT 0, subscribed INTEGER DEFAULT 0, total_paid INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_seen TEXT);
CREATE TABLE IF NOT EXISTS chats (chat_id INTEGER PRIMARY KEY, title TEXT, added_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, interval_min INTEGER NOT NULL, price INTEGER NOT NULL, status TEXT DEFAULT 'pending', confirmed_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, funded_at TEXT);
CREATE TABLE IF NOT EXISTS ads (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, text TEXT NOT NULL, interval_min INTEGER NOT NULL, price INTEGER NOT NULL, status TEXT DEFAULT 'active', created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_sent TEXT, sent_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS roles (user_id INTEGER NOT NULL, role TEXT NOT NULL, granted_by INTEGER, granted_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, role));
"""
