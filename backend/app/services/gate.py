from __future__ import annotations
import sqlite3

from fastapi import HTTPException

from app.db import connect


def period_status(conn: sqlite3.Connection, period_id: str) -> str:
    row = conn.execute(
        "SELECT status FROM periods WHERE period_id=?", (period_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"账期不存在: {period_id}")
    return row["status"]


def assert_period_writable(period_id: str) -> None:
    """所有写入口（估抄装载 / 修订 / 实抄装载 / 对齐）共用的封印闸。"""
    conn = connect()
    try:
        status = period_status(conn, period_id)
        if status == "sealed":
            raise HTTPException(409, f"账期已封印，禁止写入: {period_id}")
    finally:
        conn.close()


def assert_period_writable_conn(conn: sqlite3.Connection, period_id: str) -> None:
    if period_status(conn, period_id) == "sealed":
        raise HTTPException(409, f"账期已封印，禁止写入: {period_id}")


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()
