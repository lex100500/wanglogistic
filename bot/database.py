import sqlite3
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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            currency_from TEXT NOT NULL,
            currency_to TEXT NOT NULL,
            amount REAL NOT NULL,
            rate REAL NOT NULL,
            amount_result REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            manager_id INTEGER,
            offer_rate REAL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(tg_id)
        );

        CREATE TABLE IF NOT EXISTS order_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            manager_tg_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );

        CREATE TABLE IF NOT EXISTS profiles (
            tg_id INTEGER PRIMARY KEY,
            wechat_qr TEXT,
            alipay_qr TEXT,
            card_number TEXT,
            card_bank TEXT,
            card_holder TEXT,
            card_phone TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (tg_id) REFERENCES users(tg_id)
        );

        CREATE TABLE IF NOT EXISTS rate_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT NOT NULL,
            buy_rate REAL NOT NULL,
            sell_rate REAL NOT NULL,
            changed_by INTEGER,
            source TEXT DEFAULT 'bot',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS banned_users (
            tg_id INTEGER PRIMARY KEY,
            reason TEXT,
            banned_at TEXT DEFAULT (datetime('now'))
        );
    """)
    # Миграция: добавляем новые колонки если их нет
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN file_id TEXT")
    except Exception:
        pass
    for col in ("offer_rate REAL", "htx_rate REAL",
                "usdt_amount REAL", "cny_bought REAL", "margin_cny REAL", "margin_rub REAL",
                "terms_confirmed INTEGER DEFAULT 0",
                "manager_msg_id INTEGER", "manager_chat_id INTEGER"):
        try:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col}")
        except Exception:
            pass
    # Заполняем дефолтные курсы
    for pair, vals in DEFAULT_RATES.items():
        conn.execute(
            "INSERT OR IGNORE INTO rates (pair, buy_rate, sell_rate) VALUES (?, ?, ?)",
            (pair, vals["buy"], vals["sell"]),
        )
    # Дефолтные настройки
    for key, value in [
        ("receipt_guide_url", "https://telegra.ph/test-cheki-03-23"),
        ("main_manager", "bulievich"),
        ("rules_url", "https://telegra.ph/Pravila-ispolzovaniya-servisa-WangLogistic-03-23"),
        ("promotions_text", "📈 Актуальный курс\n\nОт 400 юаней — {курс-0.1} рублей\nОт 2000💸 — {курс-0.2}💸\nОт 8000💸 — {курс-0.3}💸\n\n💳 При оплате с Т-Банка скидка −0.1 рублей на курс"),
        ("volume_discounts", '[{"min_cny": 400, "discount": 0.1}, {"min_cny": 2000, "discount": 0.2}, {"min_cny": 8000, "discount": 0.3}]'),
        ("min_buy_amount", "400"),
        ("bank_discounts", '[{"bank": "СБЕР", "discount": 0}, {"bank": "Т-Банк", "discount": 0.1}, {"bank": "АЛЬФА", "discount": 0}, {"bank": "ВТБ", "discount": 0}, {"bank": "ОЗОН", "discount": 0}]'),
    ]:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
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


def update_rate(pair: str, buy_rate: float, sell_rate: float,
                changed_by: Optional[int] = None, source: str = "bot"):
    conn = get_conn()
    conn.execute(
        """INSERT INTO rates (pair, buy_rate, sell_rate, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(pair) DO UPDATE SET
             buy_rate = excluded.buy_rate,
             sell_rate = excluded.sell_rate,
             updated_at = datetime('now')""",
        (pair, buy_rate, sell_rate),
    )
    conn.execute(
        "INSERT INTO rate_log (pair, buy_rate, sell_rate, changed_by, source) VALUES (?, ?, ?, ?, ?)",
        (pair, buy_rate, sell_rate, changed_by, source),
    )
    conn.commit()
    conn.close()


def get_rate_log(pair: str, limit: int = 20) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM rate_log WHERE pair = ? ORDER BY created_at DESC LIMIT ?",
        (pair, limit),
    ).fetchall()
    conn.close()
    return rows


# ---- Orders ----

def create_order(user_id: int, currency_from: str, currency_to: str,
                 amount: float, rate: float, amount_result: float,
                 pay_method: Optional[str] = None, bank: Optional[str] = None) -> int:
    conn = get_conn()
    cursor = conn.execute(
        """INSERT INTO orders (user_id, currency_from, currency_to, amount, rate, amount_result, status, pay_method, bank)
           VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?)""",
        (user_id, currency_from, currency_to, amount, rate, amount_result, pay_method, bank),
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id


def get_order(order_id: str) -> Optional[sqlite3.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return row


def get_user_active_order(user_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? AND status IN ('new', 'taken', 'in_progress') LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def get_user_completed_buy_count(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM orders WHERE user_id = ? AND currency_from = 'RUB' AND status = 'completed'",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


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

def cleanup_old_photos():
    """Удаляет file_id у сообщений старше 30 дней."""
    conn = get_conn()
    conn.execute(
        "UPDATE messages SET file_id = NULL WHERE file_id IS NOT NULL "
        "AND created_at < datetime('now', '-30 days')"
    )
    conn.commit()
    conn.close()


def save_message(order_id: str, sender_id: int, text: str, file_id: Optional[str] = None):
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (order_id, sender_id, text, file_id) VALUES (?, ?, ?, ?)",
        (order_id, sender_id, text, file_id),
    )
    conn.commit()
    conn.close()


def get_all_active_managers() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM managers WHERE is_active = 1"
    ).fetchall()
    conn.close()
    return rows


def get_messages(order_id: str) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE order_id = ? ORDER BY created_at", (order_id,)
    ).fetchall()
    conn.close()
    return rows


# ---- Profiles ----

def get_profile(tg_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM profiles WHERE tg_id = ?", (tg_id,)).fetchone()
    conn.close()
    return row


def update_profile(tg_id: int, **kwargs):
    conn = get_conn()
    existing = conn.execute("SELECT 1 FROM profiles WHERE tg_id = ?", (tg_id,)).fetchone()
    if not existing:
        conn.execute("INSERT INTO profiles (tg_id) VALUES (?)", (tg_id,))
    sets = ["updated_at = datetime('now')"]
    params = []
    for key in ("wechat_qr", "alipay_qr", "card_number", "card_bank", "card_holder", "card_phone"):
        if key in kwargs:
            sets.append(f"{key} = ?")
            params.append(kwargs[key])
    params.append(tg_id)
    conn.execute(f"UPDATE profiles SET {', '.join(sets)} WHERE tg_id = ?", params)
    conn.commit()
    conn.close()


# ---- Settings ----

def update_order_margin(order_id: str, usdt_amount: float, cny_bought: float,
                        margin_cny: float, margin_rub: float):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET usdt_amount=?, cny_bought=?, margin_cny=?, margin_rub=?, updated_at=datetime('now') WHERE id=?",
        (usdt_amount, cny_bought, margin_cny, margin_rub, order_id),
    )
    conn.commit()
    conn.close()


def update_order_htx_rate(order_id: str, htx_rate: float):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET htx_rate = ?, updated_at = datetime('now') WHERE id = ?",
        (htx_rate, order_id),
    )
    conn.commit()
    conn.close()


def update_order_offer_rate(order_id: str, offer_rate: float):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET offer_rate = ?, updated_at = datetime('now') WHERE id = ?",
        (offer_rate, order_id),
    )
    conn.commit()
    conn.close()


def save_order_notification(order_id: str, manager_tg_id: int, message_id: int, chat_id: int):
    conn = get_conn()
    conn.execute(
        "INSERT INTO order_notifications (order_id, manager_tg_id, message_id, chat_id) VALUES (?, ?, ?, ?)",
        (order_id, manager_tg_id, message_id, chat_id),
    )
    conn.commit()
    conn.close()


def get_order_notifications(order_id: str) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM order_notifications WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    conn.close()
    return rows


def confirm_order_terms(order_id: str):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET terms_confirmed = 1, updated_at = datetime('now') WHERE id = ?",
        (order_id,),
    )
    conn.commit()
    conn.close()


def save_manager_message(order_id: str, manager_msg_id: int, manager_chat_id: int):
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET manager_msg_id = ?, manager_chat_id = ?, updated_at = datetime('now') WHERE id = ?",
        (manager_msg_id, manager_chat_id, order_id),
    )
    conn.commit()
    conn.close()


def get_manager_active_order(manager_id: int):
    """Возвращает активную заявку менеджера (taken или in_progress)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM orders WHERE manager_id = ? AND status IN ('taken', 'in_progress') LIMIT 1",
        (manager_id,),
    ).fetchone()
    conn.close()
    return row


def get_pending_terms_order(user_id: int):
    """Возвращает заявку со статусом taken, где клиент ещё не подтвердил условия."""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? AND status = 'taken' AND terms_confirmed = 0",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


# ---- Bans ----

def ban_user(tg_id: int, reason: str = ""):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO banned_users (tg_id, reason) VALUES (?, ?)",
        (tg_id, reason),
    )
    conn.commit()
    conn.close()


def unban_user(tg_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM banned_users WHERE tg_id = ?", (tg_id,))
    conn.commit()
    conn.close()


def is_banned(tg_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM banned_users WHERE tg_id = ?", (tg_id,)).fetchone()
    conn.close()
    return row is not None


def get_banned_users() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT b.*, u.username, u.first_name FROM banned_users b LEFT JOIN users u ON b.tg_id = u.tg_id ORDER BY b.banned_at DESC"
    ).fetchall()
    conn.close()
    return rows


def search_users(query: str, limit: int = 20) -> list:
    conn = get_conn()
    like = f"%{query}%"
    rows = conn.execute(
        """SELECT u.tg_id, u.username, u.first_name, u.created_at,
                  (SELECT COUNT(*) FROM orders WHERE user_id = u.tg_id) as orders_count,
                  (b.tg_id IS NOT NULL) as is_banned
           FROM users u
           LEFT JOIN banned_users b ON u.tg_id = b.tg_id
           WHERE u.username LIKE ? OR u.first_name LIKE ? OR CAST(u.tg_id AS TEXT) LIKE ?
           ORDER BY u.created_at DESC LIMIT ?""",
        (like, like, like, limit),
    ).fetchall()
    conn.close()
    return rows


# ---- Settings ----

def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()
