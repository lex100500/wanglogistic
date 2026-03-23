import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from bot.config import DB_PATH, DEFAULT_RATES


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS managers (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_active INTEGER DEFAULT 1,
            added_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS rates (
            pair TEXT PRIMARY KEY,
            buy_rate REAL NOT NULL,
            sell_rate REAL NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            currency_from TEXT NOT NULL,
            currency_to TEXT NOT NULL,
            amount REAL NOT NULL,
            rate REAL NOT NULL,
            amount_result REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            manager_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(tg_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            sender_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );
    """)
    # Заполняем дефолтные курсы
    for pair, vals in DEFAULT_RATES.items():
        conn.execute(
            "INSERT OR IGNORE INTO rates (pair, buy_rate, sell_rate) VALUES (?, ?, ?)",
            (pair, vals["buy"], vals["sell"]),
        )
    conn.commit()
    conn.close()


# ---- Users ----

def upsert_user(tg_id: int, username: Optional[str], first_name: Optional[str]):
    conn = get_conn()
    conn.execute(
        """INSERT INTO users (tg_id, username, first_name) VALUES (?, ?, ?)
           ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
        (tg_id, username, first_name),
    )
    conn.commit()
    conn.close()


# ---- Rates ----

def get_rate(pair: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM rates WHERE pair = ?", (pair,)).fetchone()
    conn.close()
    return row


def get_all_rates() -> list:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM rates").fetchall()
    conn.close()
    return rows


# ---- Orders ----

def create_order(user_id: int, currency_from: str, currency_to: str,
                 amount: float, rate: float, amount_result: float) -> str:
    order_id = uuid.uuid4().hex[:12]
    conn = get_conn()
    conn.execute(
        """INSERT INTO orders (id, user_id, currency_from, currency_to, amount, rate, amount_result, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'new')""",
        (order_id, user_id, currency_from, currency_to, amount, rate, amount_result),
    )
    conn.commit()
    conn.close()
    return order_id


def get_order(order_id: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return row


def get_user_orders(user_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


def update_order_status(order_id: str, status: str, manager_id: Optional[int] = None):
    conn = get_conn()
    if manager_id is not None:
        conn.execute(
            "UPDATE orders SET status = ?, manager_id = ?, updated_at = datetime('now') WHERE id = ?",
            (status, manager_id, order_id),
        )
    else:
        conn.execute(
            "UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (status, order_id),
        )
    conn.commit()
    conn.close()


# ---- Managers ----

def is_manager(tg_id: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM managers WHERE tg_id = ? AND is_active = 1", (tg_id,)
    ).fetchone()
    conn.close()
    return row is not None


def add_manager(tg_id: int, username: Optional[str], first_name: Optional[str]):
    conn = get_conn()
    conn.execute(
        """INSERT INTO managers (tg_id, username, first_name) VALUES (?, ?, ?)
           ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, is_active=1""",
        (tg_id, username, first_name),
    )
    conn.commit()
    conn.close()


# ---- Messages (relay) ----

def save_message(order_id: str, sender_id: int, text: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (order_id, sender_id, text) VALUES (?, ?, ?)",
        (order_id, sender_id, text),
    )
    conn.commit()
    conn.close()


def get_messages(order_id: str) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE order_id = ? ORDER BY created_at", (order_id,)
    ).fetchall()
    conn.close()
    return rows
