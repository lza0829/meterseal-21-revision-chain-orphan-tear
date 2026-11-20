from __future__ import annotations
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect
from app.services import estimates
from app.services.gate import assert_period_writable

router = APIRouter(tags=["readings"])


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class EstimateIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    doc_no: str = Field(default="", max_length=64)


class RevisionIn(BaseModel):
    qty: float
    reason: str = Field(min_length=1)
    revised_by: str = "system"
    doc_no: str | None = None
    expected_version_no: int | None = None


class ActualIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    occurred_at: str
    reader_code: str = "R00"
    doc_no: str = Field(min_length=1)


# --------------------------------------------------------------------------
# 估抄：列表只吐 current 版本；历史走 detail；修订走专用入口
# --------------------------------------------------------------------------
@router.get("/estimates")
def list_estimates(period_id: str | None = None):
    return estimates.list_current(period_id)


@router.get("/estimates/{estimate_id}")
def estimate_detail(estimate_id: str):
    return estimates.get_detail(estimate_id)


@router.get("/estimates/{estimate_id}/compare")
def estimate_compare(estimate_id: str, from_no: int, to_no: int):
    return estimates.compare(estimate_id, from_no, to_no)


@router.post("/estimates")
def create_estimate(body: EstimateIn):
    return estimates.create(body.period_id, body.meter_id, body.qty, body.doc_no)


@router.post("/estimates/{estimate_id}/revisions")
def revise_estimate(estimate_id: str, body: RevisionIn):
    return estimates.revise(
        estimate_id,
        body.qty,
        body.reason,
        body.revised_by,
        body.doc_no,
        body.expected_version_no,
    )


# --------------------------------------------------------------------------
# 实抄
# --------------------------------------------------------------------------
@router.get("/actuals")
def list_actuals(period_id: str | None = None):
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM actuals WHERE period_id=? ORDER BY meter_id", (period_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM actuals ORDER BY period_id, meter_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/actuals")
def create_actual(body: ActualIn):
    assert_period_writable(body.period_id)
    conn = connect()
    try:
        mid = conn.execute("SELECT 1 FROM meters WHERE meter_id=?", (body.meter_id,)).fetchone()
        if not mid:
            raise HTTPException(404, "表具不存在")
        aid = "A-" + uuid.uuid4().hex[:10]
        try:
            conn.execute(
                """INSERT INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    aid,
                    body.meter_id,
                    body.period_id,
                    body.qty,
                    body.occurred_at,
                    body.reader_code,
                    body.doc_no,
                    _now(),
                ),
            )
            conn.commit()
        except Exception as e:
            raise HTTPException(409, f"实抄写入失败: {e}") from e
        return {"actual_id": aid, "ok": True}
    finally:
        conn.close()
