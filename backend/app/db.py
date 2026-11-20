from __future__ import annotations
import os
import sqlite3
from app.config import DATA_DIR, DB_PATH

# 测试环境用 NORMAL 同步级别省 fsync，生产默认 FULL
_SYNC = "NORMAL" if os.environ.get("METERSEAL_SYNC_NORMAL") else "FULL"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA synchronous={_SYNC}")
    return conn


class transaction:
    """显式事务：BEGIN IMMEDIATE 先拿写锁，避免并发修订交错。

    isolation_level=None 时 sqlite3 不自动开事务，这里自己声明边界。
    """

    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = connect()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self.conn is not None
        try:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        finally:
            self.conn.close()
