# 监测数据与告警的 SQLite 存取
import json
import sqlite3
from datetime import datetime

from config import DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etfs (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                market INTEGER NOT NULL,
                baseline_volume REAL NOT NULL,
                baseline_amount REAL NOT NULL,
                intraday_shape TEXT NOT NULL,
                price REAL NOT NULL DEFAULT 0,
                change_pct REAL NOT NULL DEFAULT 0,
                volume REAL NOT NULL DEFAULT 0,
                amount REAL NOT NULL DEFAULT 0,
                volume_ratio REAL NOT NULL DEFAULT 0,
                large_buy_count INTEGER NOT NULL DEFAULT 0,
                large_buy_amount REAL NOT NULL DEFAULT 0,
                large_sell_amount REAL NOT NULL DEFAULT 0,
                main_net REAL NOT NULL DEFAULT 0,
                large_net REAL NOT NULL DEFAULT 0,
                super_net REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT '',
                selected_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                group_name TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                level TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                metrics TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'live',
                trade_date TEXT NOT NULL,
                minute TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_created ON alerts(created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS big_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                seq INTEGER NOT NULL,
                tick_time TEXT NOT NULL,
                price REAL NOT NULL,
                volume REAL NOT NULL,
                amount REAL NOT NULL,
                side TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(code, trade_date, seq)
            )
            """
        )


def replace_etfs(rows: list[dict]) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute("DELETE FROM etfs")
        for row in rows:
            conn.execute(
                """
                INSERT INTO etfs (
                    code, name, group_name, market, baseline_volume, baseline_amount,
                    intraday_shape, selected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["code"],
                    row["name"],
                    row["group_name"],
                    row["market"],
                    row["baseline_volume"],
                    row["baseline_amount"],
                    json.dumps(row["shape"], ensure_ascii=False),
                    stamp,
                ),
            )


def list_etfs() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM etfs ORDER BY group_name, code").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["shape"] = json.loads(item["intraday_shape"])
        items.append(item)
    return items


def update_snapshot(
    code: str,
    price: float,
    change_pct: float,
    volume: float,
    amount: float,
    volume_ratio: float,
    updated_at: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE etfs SET price = ?, change_pct = ?, volume = ?, amount = ?,
                volume_ratio = ?, updated_at = ?
            WHERE code = ?
            """,
            (price, change_pct, volume, amount, volume_ratio, updated_at, code),
        )


def update_fund_flow(code: str, main_net: float, large_net: float, super_net: float) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE etfs SET main_net = ?, large_net = ?, super_net = ? WHERE code = ?",
            (main_net, large_net, super_net, code),
        )


def insert_big_tick(
    code: str,
    trade_date: str,
    seq: int,
    tick_time: str,
    price: float,
    volume: float,
    amount: float,
    side: str,
) -> bool:
    stamp = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO big_ticks (
                code, trade_date, seq, tick_time, price, volume, amount, side, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code, trade_date, seq, tick_time, price, volume, amount, side, stamp),
        )
        inserted = cursor.rowcount > 0
        if inserted:
            if side == "B":
                conn.execute(
                    """
                    UPDATE etfs SET large_buy_count = large_buy_count + 1,
                        large_buy_amount = large_buy_amount + ?
                    WHERE code = ?
                    """,
                    (amount, code),
                )
            else:
                conn.execute(
                    "UPDATE etfs SET large_sell_amount = large_sell_amount + ? WHERE code = ?",
                    (amount, code),
                )
    return inserted


def insert_alert(
    code: str,
    name: str,
    group_name: str,
    alert_type: str,
    level: str,
    title: str,
    detail: str,
    metrics: dict,
    source: str,
    trade_date: str,
    minute: str,
) -> int:
    stamp = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO alerts (
                code, name, group_name, alert_type, level, title, detail, metrics,
                source, trade_date, minute, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                name,
                group_name,
                alert_type,
                level,
                title,
                detail,
                json.dumps(metrics, ensure_ascii=False),
                source,
                trade_date,
                minute,
                stamp,
            ),
        )
        return int(cursor.lastrowid)


def list_alerts(group_name: str, alert_type: str, limit: int = 100) -> list[dict]:
    query = "SELECT * FROM alerts"
    conditions = []
    params: list = []
    if group_name:
        conditions.append("group_name = ?")
        params.append(group_name)
    if alert_type:
        conditions.append("alert_type = ?")
        params.append(alert_type)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["metrics"] = json.loads(item["metrics"])
        items.append(item)
    return items


def mark_alert_read(alert_id: int) -> bool:
    with _connect() as conn:
        cursor = conn.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))
        return cursor.rowcount > 0


def has_review_alert(code: str, alert_type: str, trade_date: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM alerts
            WHERE code = ? AND alert_type = ? AND trade_date = ? AND source = 'review'
            LIMIT 1
            """,
            (code, alert_type, trade_date),
        ).fetchone()
        return row is not None


def last_alert_time(code: str, alert_type: str) -> datetime | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT created_at FROM alerts
            WHERE code = ? AND alert_type = ? AND source = 'live'
            ORDER BY id DESC LIMIT 1
            """,
            (code, alert_type),
        ).fetchone()
    if row is None:
        return None
    try:
        return datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None


def count_big_ticks(trade_date: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM big_ticks WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
    return int(row["total"]) if row else 0


def count_alerts(trade_date: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM alerts WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
    return int(row["total"]) if row else 0
