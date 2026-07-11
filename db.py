"""
Простой слой хранения на SQLite.
Хранит сценарии (scenarios) и заказы (orders).
Файл базы: constructor.db (лежит рядом с приложением).
На Render free tier диск эфемерный — при редеплое/засыпании
данные могут теряться. Для продакшена вынести в Supabase/Postgres.
"""
import sqlite3
import json
import time
import uuid
from contextlib import contextmanager

DB_PATH = "constructor.db"


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scenarios (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                config TEXT NOT NULL,
                created_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                user_id INTEGER,
                tariff TEXT,        -- 'direct' (5р) или 'cryptobot' (4р)
                status TEXT,        -- 'pending' | 'paid' | 'delivered'
                payment_ref TEXT,   -- id инвойса в cryptobot, если есть
                created_at REAL
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def save_scenario(user_id: int, config: dict) -> str:
    scenario_id = str(uuid.uuid4())[:8]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO scenarios (id, user_id, config, created_at) VALUES (?, ?, ?, ?)",
            (scenario_id, user_id, json.dumps(config, ensure_ascii=False), time.time()),
        )
        conn.commit()
    return scenario_id


def get_scenario(scenario_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "user_id": row["user_id"], "config": json.loads(row["config"])}


def create_order(scenario_id: str, user_id: int, tariff: str) -> str:
    order_id = str(uuid.uuid4())[:8]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO orders (id, scenario_id, user_id, tariff, status, payment_ref, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', NULL, ?)",
            (order_id, scenario_id, user_id, tariff, time.time()),
        )
        conn.commit()
    return order_id


def get_order(order_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None


def set_order_status(order_id: str, status: str, payment_ref: str | None = None):
    with get_conn() as conn:
        if payment_ref is not None:
            conn.execute(
                "UPDATE orders SET status = ?, payment_ref = ? WHERE id = ?",
                (status, payment_ref, order_id),
            )
        else:
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        conn.commit()
