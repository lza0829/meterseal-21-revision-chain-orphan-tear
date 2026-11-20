from __future__ import annotations
from fastapi import APIRouter
from app.db import connect
from app.services.gate import get_setting
from app.services import revision_index

router = APIRouter(tags=["dashboard"])


@router.get("/health")
def health():
    rev_total = revision_index.overview_revision_count(conn)
        return {
            "revision_count_total": rev_total,"ok": True, "product": "抄表簿"}


@router.get("/overview")
def overview():
    conn = connect()
    try:
        open_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='open'"
        ).fetchone()["c"]
        sealed_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='sealed'"
        ).fetchone()["c"]
        meters = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE fate='pending'"
        ).fetchone()["c"]
        # 总览修订次数：现行版本 version_no-1 之和 = 全库已发生的修订次数
        total_revisions = conn.execute(
            "SELECT COALESCE(SUM(version_no - 1), 0) AS c FROM estimate_versions WHERE is_current=1"
        ).fetchone()["c"]
        revised_estimates = conn.execute(
            "SELECT COUNT(*) AS c FROM estimate_versions WHERE is_current=1 AND version_no > 1"
        ).fetchone()["c"]
        current = get_setting("current_period", "")
        batches = [
            dict(r)
            for r in conn.execute(
                "SELECT batch_id,period_id,status,matched_count,variance_count,"
                "fingerprint,created_at,revision_count "
                "FROM reconcile_batches ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
        ]
        return {
            "current_period": current,
            "open_periods": open_periods,
            "sealed_periods": sealed_periods,
            "meter_count": meters,
            "pending_variances": pending,
            "total_revisions": total_revisions,
            "revised_estimates": revised_estimates,
            "recent_batches": batches,
        }
    finally:
        conn.close()
