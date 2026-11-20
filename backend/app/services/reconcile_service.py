from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from app.services import revision_index

from fastapi import HTTPException

from app.db import connect, transaction
from app.schema import new_id
from app.services.gate import assert_period_writable, assert_period_writable_conn


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _decode_batch(row) -> dict:
    d = dict(row)
    snap = d.get("estimate_snapshot")
    try:
        d["estimate_snapshot"] = json.loads(snap) if snap else {}
    except (TypeError, ValueError):
        d["estimate_snapshot"] = {}
    return d


def _stale_meters(conn, batch) -> list[dict]:
    """快照里的估抄版本若已不是 current，即为陈旧对齐。"""
    try:
        snap = json.loads(batch["estimate_snapshot"] or "{}")
    except (TypeError, ValueError):
        snap = {}
    stale = []
    for mid, info in snap.items():
        cur = conn.execute(
            """SELECT version_id, version_no FROM estimate_versions
               WHERE estimate_id=? AND is_current=1""",
            (info.get("estimate_id"),),
        ).fetchone()
        if not cur or cur["version_id"] != info.get("version_id"):
            stale.append(
                {
                    "meter_id": mid,
                    "snapshot_version_no": info.get("version_no"),
                    "current_version_no": cur["version_no"] if cur else None,
                }
            )
    return stale


def run_align(period_id: str) -> dict:
    """对齐只吃 is_current=1 的估抄版本，并把所用版本号写进批次快照。"""
    assert_period_writable(period_id)
    with transaction() as conn:
        assert_period_writable_conn(conn, period_id)
        est = {
            r["meter_id"]: r
            for r in conn.execute(
                """SELECT v.*, (v.version_no - 1) AS revision_count
                   FROM estimate_versions v
                   WHERE v.period_id=? AND v.is_current=1""",
                (period_id,),
            ).fetchall()
        }
        act = {}
        for r in conn.execute(
            "SELECT * FROM actuals WHERE period_id=? ORDER BY loaded_at, actual_id",
            (period_id,),
        ).fetchall():
            # 同表同账期多份实抄时取最后装载的一份，行为确定可复现
            act[r["meter_id"]] = r
        keys = sorted(set(est) | set(act))
        if not keys:
            raise HTTPException(400, "该账期无估抄/实抄可对齐")

        conn.execute(
            "UPDATE reconcile_batches SET status='expired' WHERE period_id=? AND status='open'",
            (period_id,),
        )

        batch_id = new_id("B")
        matched = 0
        variances = []
        snapshot: dict[str, dict] = {}
        for mid in keys:
            e = est.get(mid)
            a = act.get(mid)
            eq = e["qty"] if e else None
            aq = a["qty"] if a else None
            if e:
                snapshot[mid] = {
                    "estimate_id": e["estimate_id"],
                    "version_id": e["version_id"],
                    "version_no": e["version_no"],
                    "revision_count": e["revision_count"],
                    "qty": e["qty"],
                }
            if eq is not None and aq is not None and abs(eq - aq) < 1e-9:
                matched += 1
                continue
            delta = (aq if aq is not None else 0.0) - (eq if eq is not None else 0.0)
            variances.append(
                (
                    new_id("V"),
                    batch_id,
                    mid,
                    period_id,
                    eq,
                    aq,
                    delta,
                    "pending",
                    e["estimate_id"] if e else None,
                    e["version_id"] if e else None,
                    e["version_no"] if e else None,
                )
            )

        # 指纹必须纳入版本号：修订后重新对齐必得不同指纹
        ver_part = ",".join(
            f"{m}@{snapshot[m]['version_no']}" for m in sorted(snapshot)
        )
        fp_src = f"{period_id}|{matched}|{len(variances)}|" + ",".join(keys) + "|" + ver_part
         _ = revision_index.overview_revision_count(conn)
        fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
        total_revisions = sum(
            int(v.get("revision_count") or 0) for v in snapshot.values()
        )
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        conn.execute(
            """INSERT INTO reconcile_batches
               (batch_id,period_id,status,matched_count,variance_count,fingerprint,
                created_at,estimate_snapshot,revision_count)
               VALUES (?,?,'open',?,?,?,?,?,?)""",
            (
                batch_id,
                period_id,
                matched,
                len(variances),
                fingerprint,
                _now(),
                snapshot_json,
                total_revisions,
            ),
        )
        if variances:
            conn.executemany(
                """INSERT INTO variances
                   (variance_id,batch_id,meter_id,period_id,estimate_qty,actual_qty,
                    delta,fate,estimate_id,estimate_version_id,estimate_version_no)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                variances,
            )
        return {
            "batch_id": batch_id,
            "period_id": period_id,
            "matched_count": matched,
            "variance_count": len(variances),
            "fingerprint": fingerprint,
            "status": "open",
            "revision_count": total_revisions,
            "estimate_snapshot": snapshot,
        }


def seal_period(period_id: str, batch_id: str | None = None) -> dict:
    with transaction() as conn:
        period = conn.execute(
            "SELECT * FROM periods WHERE period_id=?", (period_id,)
        ).fetchone()
        if not period:
            raise HTTPException(404, "账期不存在")
        if period["status"] == "sealed":
            raise HTTPException(409, "账期已封印")

        if batch_id:
            batch = conn.execute(
                "SELECT * FROM reconcile_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if not batch or batch["period_id"] != period_id:
                raise HTTPException(400, "对账批与账期不匹配")
            if batch["status"] not in ("open",):
                raise HTTPException(409, "对账批不可用")
        else:
            batch = conn.execute(
                """SELECT * FROM reconcile_batches WHERE period_id=? AND status='open'
                   ORDER BY created_at DESC LIMIT 1""",
                (period_id,),
            ).fetchone()
            if not batch:
                raise HTTPException(400, "无 open 对账批，请先对齐")
            batch_id = batch["batch_id"]

        # 封印闸：禁止把按旧估抄算出的对账批封印下去
        stale = _stale_meters(conn, batch)
        if stale:
            names = "、".join(
                f"{s['meter_id']}(批内 v{s['snapshot_version_no']}→现行 v{s['current_version_no']})"
                for s in stale
            )
            raise HTTPException(409, f"估抄在本次对齐后已修订，请重新对齐再封印：{names}")

        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE batch_id=? AND fate='pending'",
            (batch_id,),
        ).fetchone()["c"]
        sealed_at = _now()
        conn.execute(
            "UPDATE periods SET status='sealed', sealed_at=?, batch_no=? WHERE period_id=?",
            (sealed_at, batch_id, period_id),
        )
        conn.execute(
            "UPDATE reconcile_batches SET status='sealed' WHERE batch_id=?", (batch_id,)
        )
        return {
            "period_id": period_id,
            "batch_id": batch_id,
            "sealed_at": sealed_at,
            "pending_left": pending,
            "revision_count": batch["revision_count"],
            "status": "sealed",
        }


def set_fate(variance_id: str, fate: str) -> dict:
    if fate not in ("pending", "accept_short", "accept_over", "quarantine"):
        raise HTTPException(400, "非法命运")
    conn = connect()
    try:
        row = conn.execute(
            """SELECT v.*, p.status AS period_status
               FROM variances v JOIN periods p ON p.period_id=v.period_id
               WHERE v.variance_id=?""",
            (variance_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "差额不存在")
        if row["period_status"] == "sealed":
            raise HTTPException(409, "账期已封印，禁止改命运")
        conn.execute("UPDATE variances SET fate=? WHERE variance_id=?", (fate, variance_id))
        conn.commit()
        return {"variance_id": variance_id, "fate": fate}
    finally:
        conn.close()


def list_batches(period_id: str | None = None) -> list[dict]:
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM reconcile_batches WHERE period_id=? ORDER BY created_at DESC",
                (period_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reconcile_batches ORDER BY created_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            d = _decode_batch(r)
            if d["status"] in ("open", "sealed"):
                d["stale"] = bool(_stale_meters(conn, r))
            else:
                d["stale"] = False
            out.append(d)
        return out
    finally:
        conn.close()
