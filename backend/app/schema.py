from __future__ import annotations
import sqlite3
import uuid

# ---------------------------------------------------------------------------
# 估抄修订架构：版本链（全局唯一口径）
#
#   每次修订往 estimate_versions 插入新版本行，旧版本翻为 is_current=0 并记
#   superseded_at；对齐只读 is_current=1；历史版本永久保留可查；已封印账期
#   拒绝修订；对齐批快照记录每只表的 version_no / revision_count。
#
#   estimate_id 是一条估抄的稳定身份，version_no 从 1 起递增，
#   revision_count = 当前 version_no - 1。
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meters (
  meter_id TEXT PRIMARY KEY,
  building TEXT NOT NULL,
  unit_label TEXT NOT NULL,
  meter_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS periods (
  period_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'open',
  sealed_at TEXT,
  batch_no TEXT
);
CREATE TABLE IF NOT EXISTS estimate_versions (
  version_id TEXT PRIMARY KEY,
  estimate_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  version_no INTEGER NOT NULL CHECK (version_no >= 1),
  qty REAL NOT NULL,
  doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1)),
  superseded_at TEXT,
  revision_reason TEXT,
  revised_by TEXT,
  UNIQUE(estimate_id, version_no),
  UNIQUE(estimate_id, doc_no),
  UNIQUE(period_id, doc_no)
);
-- 一条逻辑估抄只有一个 current 版本；同表同账期也只有一个 current 估抄。
CREATE UNIQUE INDEX IF NOT EXISTS ux_ev_current_estimate
  ON estimate_versions(estimate_id) WHERE is_current=1;
CREATE UNIQUE INDEX IF NOT EXISTS ux_ev_current_meter_period
  ON estimate_versions(meter_id, period_id) WHERE is_current=1;
CREATE INDEX IF NOT EXISTS ix_ev_period ON estimate_versions(period_id, is_current);
CREATE INDEX IF NOT EXISTS ix_ev_estimate ON estimate_versions(estimate_id, version_no);
CREATE TABLE IF NOT EXISTS actuals (
  actual_id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT NOT NULL,
  reader_code TEXT NOT NULL,
  doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE IF NOT EXISTS reconcile_batches (
  batch_id TEXT PRIMARY KEY,
  period_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  matched_count INTEGER NOT NULL DEFAULT 0,
  variance_count INTEGER NOT NULL DEFAULT 0,
  fingerprint TEXT,
  created_at TEXT NOT NULL,
  -- 对齐时锁定的估抄版本快照（JSON：meter_id -> {estimate_id,version_id,version_no,revision_count,qty}）
  estimate_snapshot TEXT,
  -- 快照内各估抄已发生的修订次数合计
  revision_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS variances (
  variance_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  estimate_qty REAL,
  actual_qty REAL,
  delta REAL NOT NULL,
  fate TEXT NOT NULL DEFAULT 'pending',
  -- 产生该差额的估抄版本（可审计到具体版本）
  estimate_id TEXT,
  estimate_version_id TEXT,
  estimate_version_no INTEGER
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# 旧库补列：CREATE TABLE IF NOT EXISTS 不会给已有表加列
_ADD_COLUMNS = {
    "reconcile_batches": [
        ("estimate_snapshot", "TEXT"),
        ("revision_count", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "variances": [
        ("estimate_id", "TEXT"),
        ("estimate_version_id", "TEXT"),
        ("estimate_version_no", "INTEGER"),
    ],
}


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, cols in _ADD_COLUMNS.items():
        if not _table_exists(conn, table):
            continue
        have = _existing_columns(conn, table)
        for name, ddl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def migrate_legacy_estimates(conn: sqlite3.Connection) -> int:
    """把旧版单表 estimates 导入版本链：同表同账期的第一份单号作为 v1 现行，
    其余单号按装载时刻依次作为历史版本（is_current=0），不丢任何行。"""
    if not _table_exists(conn, "estimates"):
        return 0
    have = _existing_columns(conn, "estimates")
    if not {"estimate_id", "meter_id", "period_id", "qty", "doc_no"} <= have:
        return 0
    rows = conn.execute(
        "SELECT * FROM estimates ORDER BY period_id, meter_id, loaded_at, estimate_id"
    ).fetchall()
    if not rows:
        return 0

    imported = 0
    seen: dict[tuple[str, str], tuple[str, int]] = {}
    for r in rows:
        key = (r["meter_id"], r["period_id"])
        eid, next_no = seen.get(key, (r["estimate_id"], 1))
        if key in seen:
            # 同一表同账期的第二份单据 -> 同一条估抄的身份继续挂版本
            next_no += 1
        seen[key] = (eid, next_no)
        exists = conn.execute(
            "SELECT 1 FROM estimate_versions WHERE version_id=?",
            ("V-" + str(r["estimate_id"]),),
        ).fetchone()
        if exists:
            continue
        loaded_at = r["loaded_at"] if "loaded_at" in have and r["loaded_at"] else ""
        # 先全部按历史版插入，末尾再把每条估抄的最高版本翻成 current
        conn.execute(
            """INSERT OR IGNORE INTO estimate_versions
               (version_id,estimate_id,meter_id,period_id,version_no,qty,doc_no,
                loaded_at,is_current,superseded_at,revision_reason,revised_by)
               VALUES (?,?,?,?,?,?,?,?,0,?,?,?)""",
            (
                "V-" + str(r["estimate_id"]),
                eid,
                r["meter_id"],
                r["period_id"],
                next_no,
                r["qty"],
                r["doc_no"],
                loaded_at,
                loaded_at,
                "旧库导入",
                "system",
            ),
        )
        imported += 1
    if imported:
        conn.execute(
            """UPDATE estimate_versions SET is_current=1, superseded_at=NULL,
                      revision_reason=NULL, revised_by='旧库导入'
               WHERE is_current=0 AND version_no = (
                 SELECT MAX(v2.version_no) FROM estimate_versions v2
                 WHERE v2.estimate_id = estimate_versions.estimate_id)"""
        )
    conn.execute("ALTER TABLE estimates RENAME TO estimates_legacy")
    return imported


def bootstrap(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    migrate_legacy_estimates(conn)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"
