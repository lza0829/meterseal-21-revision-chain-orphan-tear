from __future__ import annotations
from app.db import connect
from app.schema import bootstrap

DEMO_METERS = [
    ("M-W-101", "A栋", "101", "water"),
    ("M-W-102", "A栋", "102", "water"),
    ("M-E-101", "A栋", "101", "electric"),
    ("M-W-201", "B栋", "201", "water"),
    ("M-E-201", "B栋", "201", "electric"),
    ("M-E-202", "B栋", "202", "electric"),
]

DEMO_ESTIMATES = [
    ("E1", "M-W-101", "2026-10", 12.0, "EST-101", "2026-10-01T08:00:00"),
    ("E2", "M-W-102", "2026-10", 15.0, "EST-102", "2026-10-01T08:00:00"),
    ("E3", "M-E-101", "2026-10", 220.0, "EST-E101", "2026-10-01T08:00:00"),
    ("E4", "M-W-201", "2026-10", 18.0, "EST-201", "2026-10-01T08:00:00"),
    ("E5", "M-E-201", "2026-10", 310.0, "EST-E201", "2026-10-01T08:00:00"),
    ("E6", "M-E-202", "2026-10", 280.0, "EST-E202", "2026-10-01T08:00:00"),
]

DEMO_ACTUALS = [
    ("A1", "M-W-101", "2026-10", 12.0, "2026-10-28T10:00:00", "R01", "ACT-101", "2026-10-28T10:05:00"),
    ("A2", "M-W-102", "2026-10", 14.0, "2026-10-28T10:10:00", "R01", "ACT-102", "2026-10-28T10:15:00"),  # short
    ("A3", "M-E-101", "2026-10", 220.0, "2026-10-28T11:00:00", "R02", "ACT-E101", "2026-10-28T11:05:00"),
    ("A4", "M-W-201", "2026-10", 18.0, "2026-10-28T11:20:00", "R02", "ACT-201", "2026-10-28T11:25:00"),
    ("A5", "M-E-201", "2026-10", 330.0, "2026-10-28T12:00:00", "R03", "ACT-E201", "2026-10-28T12:05:00"),  # over
    ("A6", "M-E-202", "2026-10", 280.0, "2026-10-28T12:20:00", "R03", "ACT-E202", "2026-10-28T12:25:00"),
]


def seed_if_empty() -> None:
    """首次启动灌演示数据。

    已有业务数据（如旧库导入的估抄版本）时不再灌估抄/实抄演示行，
    但仍补齐缺失的表具与账期，避免旧库缺基础维表。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        bootstrap(conn)
        has_meters = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"] > 0
        if not has_meters:
            conn.executemany(
                "INSERT INTO meters(meter_id,building,unit_label,meter_type,status) VALUES (?,?,?,?, 'active')",
                DEMO_METERS,
            )
        for pid in ("2026-10", "2026-09"):
            conn.execute(
                "INSERT OR IGNORE INTO periods(period_id,status) VALUES (?, 'open')", (pid,)
            )

        has_estimates = (
            conn.execute("SELECT COUNT(*) AS c FROM estimate_versions").fetchone()["c"] > 0
        )
        if not has_estimates:
            # 估抄都作为版本链的 v1 装载
            conn.executemany(
                """INSERT INTO estimate_versions
                   (version_id,estimate_id,meter_id,period_id,version_no,qty,doc_no,
                    loaded_at,is_current,revised_by)
                   VALUES (?,?,?,?,1,?,?,?,1,'seed')""",
                [(f"EV-{e[0]}", e[0], e[1], e[2], e[3], e[4], e[5]) for e in DEMO_ESTIMATES],
            )
            conn.executemany(
                "INSERT INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at) VALUES (?,?,?,?,?,?,?,?)",
                DEMO_ACTUALS,
            )
            conn.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES ('current_period','2026-10')"
            )
        # 界面不露英文：表类型仍存 water/electric 码，前端映射为中文
        conn.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES ('product_title','抄表簿')"
        )
        if not conn.execute("SELECT 1 FROM settings WHERE key='current_period'").fetchone():
            conn.execute(
                "INSERT INTO settings(key,value) VALUES ('current_period','2026-10')"
            )
        conn.commit()
    finally:
        conn.close()
