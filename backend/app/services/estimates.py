from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from app.services import revision_index

from fastapi import HTTPException

from app.db import transaction, connect
from app.schema import new_id
from app.services.gate import assert_period_writable_conn


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_CURRENT_SELECT = """
SELECT v.version_id, v.estimate_id, v.meter_id, v.period_id, v.version_no,
       v.qty, v.doc_no, v.loaded_at, v.is_current, v.superseded_at,
       v.revision_reason, v.revised_by,
       (v.version_no - 1) AS revision_count
FROM estimate_versions v
WHERE v.is_current=1
"""


def list_current(period_id: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                _CURRENT_SELECT + " AND v.period_id=? ORDER BY v.meter_id",
                (period_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                _CURRENT_SELECT + " ORDER BY v.period_id, v.meter_id"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _versions(conn, estimate_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT version_id, estimate_id, meter_id, period_id, version_no, qty,
                  doc_no, loaded_at, is_current, superseded_at, revision_reason,
                  revised_by
           FROM estimate_versions WHERE estimate_id=? ORDER BY version_no""",
        (estimate_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_detail(estimate_id: str) -> dict[str, Any]:
    conn = connect()
    try:
        versions = _versions(conn, estimate_id)
        if not versions:
            raise HTTPException(404, "估抄不存在")
        current = next((v for v in versions if v["is_current"]), None)
        detail = {
            "estimate_id": estimate_id,
            "meter_id": versions[0]["meter_id"],
            "period_id": versions[0]["period_id"],
            "current": current,
            "revision_count": len(versions) - 1,
            "versions": versions,
        }
        return revision_index.detail_overlay(conn, detail)
    finally:
        conn.close()




def create(period_id: str, meter_id: str, qty: float, doc_no: str,
           revised_by: str = "system") -> dict[str, Any]:
    """装载一份估抄（v1，current=1）。同表同账期已有 current 时必须走修订。"""
    with transaction() as conn:
        assert_period_writable_conn(conn, period_id)
        if not conn.execute("SELECT 1 FROM meters WHERE meter_id=?", (meter_id,)).fetchone():
            raise HTTPException(404, "表具不存在")

        occupied = conn.execute(
            """SELECT estimate_id, version_no FROM estimate_versions
               WHERE meter_id=? AND period_id=? AND is_current=1""",
            (meter_id, period_id),
        ).fetchone()
        if occupied:
            raise HTTPException(
                409,
                f"该表该账期已有现行估抄 {occupied['estimate_id']}"
                f"（第 {occupied['version_no']} 版），请使用修订入口",
            )
        doc_no = doc_no or new_id("EST")
        if conn.execute(
            "SELECT 1 FROM estimate_versions WHERE period_id=? AND doc_no=?",
            (period_id, doc_no),
        ).fetchone():
            raise HTTPException(409, f"单号已存在: {doc_no}")

        eid = new_id("E")
        vid = new_id("EV")
        conn.execute(
            """INSERT INTO estimate_versions
               (version_id,estimate_id,meter_id,period_id,version_no,qty,doc_no,
                loaded_at,is_current,revised_by)
               VALUES (?,?,?,?,1,?,?,?,1,?)""",
            (vid, eid, meter_id, period_id, qty, doc_no, _now(), revised_by),
        )
        return {
            "estimate_id": eid,
            "version_id": vid,
            "version_no": 1,
            "revision_count": 0,
            "qty": qty,
            "ok": True,
        }


def revise(
    estimate_id: str,
    qty: float,
    reason: str,
    revised_by: str,
    doc_no: str | None = None,
    expected_version_no: int | None = None,
) -> dict[str, Any]:
    """修订入口：插入新版本并把旧版本翻为历史；全程单事务。

    - 已封印账期 -> 409，绝不改数
    - expected_version_no 不一致 -> 409（并发修订的乐观并发闸）
    - 唯一索引兜底：同一 estimate 至多一个 current
    """
    if not reason or not reason.strip():
        raise HTTPException(400, "修订必须填写原因")
    with transaction() as conn:
        head = conn.execute(
            """SELECT * FROM estimate_versions
               WHERE estimate_id=? AND is_current=1""",
            (estimate_id,),
        ).fetchone()
        if not head:
            any_row = conn.execute(
                "SELECT 1 FROM estimate_versions WHERE estimate_id=?", (estimate_id,)
            ).fetchone()
            if not any_row:
                raise HTTPException(404, "估抄不存在")
            raise HTTPException(409, "估抄无现行版本，数据异常，拒绝修订")

        assert_period_writable_conn(conn, head["period_id"])

        if expected_version_no is not None and head["version_no"] != expected_version_no:
            raise HTTPException(
                409,
                f"版本已被他人修订：期望第 {expected_version_no} 版，"
                f"现有第 {head['version_no']} 版，请刷新后重试",
            )

        next_no = conn.execute(
            "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM estimate_versions WHERE estimate_id=?",
            (estimate_id,),
        ).fetchone()["n"]

        doc_no = doc_no or new_id("REV")
        if conn.execute(
            "SELECT 1 FROM estimate_versions WHERE period_id=? AND doc_no=?",
            (head["period_id"], doc_no),
        ).fetchone():
            raise HTTPException(409, f"单号已存在: {doc_no}")

        now = _now()
        conn.execute(
            """UPDATE estimate_versions
               SET is_current=0, superseded_at=?
               WHERE estimate_id=? AND is_current=1""",
            (now, estimate_id),
        )
        vid = new_id("EV")
        conn.execute(
            """INSERT INTO estimate_versions
               (version_id,estimate_id,meter_id,period_id,version_no,qty,doc_no,
                loaded_at,is_current,revision_reason,revised_by)
               VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
            (
                vid,
                estimate_id,
                head["meter_id"],
                head["period_id"],
                next_no,
                qty,
                doc_no,
                now,
                reason.strip(),
                revised_by,
            ),
        )
        return {
            "estimate_id": estimate_id,
            "version_id": vid,
            "version_no": next_no,
            "revision_count": next_no - 1,
            "previous_qty": head["qty"],
            "qty": qty,
            "reason": reason.strip(),
            "ok": True,
        }


def compare(estimate_id: str, from_no: int, to_no: int) -> dict[str, Any]:
    conn = connect()
    try:
        versions = _versions(conn, estimate_id)
        if not versions:
            raise HTTPException(404, "估抄不存在")
        by_no = {v["version_no"]: v for v in versions}
        if from_no not in by_no or to_no not in by_no:
            raise HTTPException(404, "版本号不存在")
        a, b = by_no[from_no], by_no[to_no]
        return {
            "estimate_id": estimate_id,
            "from": a,
            "to": b,
            "delta": b["qty"] - a["qty"],
        }
    finally:
        conn.close()
