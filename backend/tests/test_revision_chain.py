from __future__ import annotations
def ok(r):
    assert r.status_code < 300, r.text
    return r.json()


def current(c, period="2026-10"):
    return {r["meter_id"]: r for r in ok(c.get("/api/estimates", params={"period_id": period}))}


def align(c, period="2026-10"):
    return ok(c.post("/api/align", json={"period_id": period}))


def revise(c, estimate_id, qty, reason="复核实抄后更正", **kw):
    return c.post(
        f"/api/estimates/{estimate_id}/revisions",
        json={"qty": qty, "reason": reason, **kw},
    )


# ---------------------------------------------------------------------------
# 架构不变量
# ---------------------------------------------------------------------------
def test_list_only_returns_current_version(client):
    rows = ok(client.get("/api/estimates"))
    assert len(rows) == 6
    assert all(r["is_current"] == 1 for r in rows)
    assert all(r["version_no"] == 1 and r["revision_count"] == 0 for r in rows)


def test_revision_creates_new_version_and_keeps_old(client):
    e = current(client)["M-W-102"]
    r = ok(revise(client, e["estimate_id"], 14.0, revised_by="张三"))
    assert r["version_no"] == 2 and r["revision_count"] == 1
    assert r["previous_qty"] == 15.0

    detail = ok(client.get(f"/api/estimates/{e['estimate_id']}"))
    assert detail["revision_count"] == 1
    assert [v["version_no"] for v in detail["versions"]] == [1, 2]
    old, new = detail["versions"]
    assert old["qty"] == 15.0 and old["is_current"] == 0 and old["superseded_at"]
    assert new["qty"] == 14.0 and new["is_current"] == 1 and new["revised_by"] == "张三"

    # 列表仍只有一行，且是现行新值
    now = current(client)["M-W-102"]
    assert now["qty"] == 14.0 and now["version_id"] == r["version_id"]


def test_compare_endpoint(client):
    e = current(client)["M-W-102"]["estimate_id"]
    ok(revise(client, e, 14.0))
    ok(revise(client, e, 13.5))
    d = ok(client.get(f"/api/estimates/{e}/compare", params={"from_no": 1, "to_no": 3}))
    assert d["from"]["qty"] == 15.0 and d["to"]["qty"] == 13.5
    assert d["delta"] == -1.5


def test_revision_requires_reason(client):
    e = current(client)["M-W-101"]["estimate_id"]
    r = client.post(f"/api/estimates/{e}/revisions", json={"qty": 1, "reason": ""})
    assert r.status_code == 422


def test_create_second_estimate_for_same_meter_period_is_rejected(client):
    r = client.post(
        "/api/estimates",
        json={"meter_id": "M-W-101", "period_id": "2026-10", "qty": 99, "doc_no": "X1"},
    )
    assert r.status_code == 409
    assert "修订入口" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 对齐吃的是修订后的现行版本
# ---------------------------------------------------------------------------
def test_align_uses_revised_qty_and_records_versions(client):
    est = current(client)["M-W-102"]
    assert align(client)["variance_count"] == 2  # 15 vs 14, 310 vs 330

    ok(revise(client, est["estimate_id"], 14.0))
    b = align(client)
    # 修订后 M-W-102 估抄=实抄，差额只剩 M-E-201
    assert b["variance_count"] == 1
    assert b["matched_count"] == 5
    assert b["estimate_snapshot"]["M-W-102"]["version_no"] == 2
    assert b["revision_count"] == 1

    vars_ = ok(client.get("/api/variances", params={"batch_id": b["batch_id"]}))
    assert [v["meter_id"] for v in vars_] == ["M-E-201"]
    snap = ok(client.get(f"/api/batches/{b['batch_id']}/snapshot"))
    assert snap["stale_meters"] == []


def test_stale_batch_flagged_after_revision(client):
    b = align(client)
    assert ok(client.get("/api/batches"))[0]["stale"] is False

    est = current(client)["M-W-102"]["estimate_id"]
    ok(revise(client, est, 14.0))

    listed = ok(client.get("/api/batches", params={"period_id": "2026-10"}))
    assert listed[0]["batch_id"] == b["batch_id"] and listed[0]["stale"] is True
    snap = ok(client.get(f"/api/batches/{b['batch_id']}/snapshot"))
    assert snap["stale_meters"][0]["meter_id"] == "M-W-102"
    # 旧批内的差额仍留着旧估抄值，用于审计
    vars_ = ok(client.get("/api/variances", params={"batch_id": b["batch_id"]}))
    short = next(v for v in vars_ if v["meter_id"] == "M-W-102")
    assert short["estimate_qty"] == 15.0 and short["estimate_version_no"] == 1


def test_seal_rejects_stale_batch_then_passes_after_realign(client):
    b = align(client)
    est = current(client)["M-W-102"]["estimate_id"]
    ok(revise(client, est, 14.0))

    r = client.post("/api/seal", json={"period_id": "2026-10", "batch_id": b["batch_id"]})
    assert r.status_code == 409 and "重新对齐" in r.json()["detail"]

    b2 = align(client)
    # 旧开放批作废，新批可封印
    batches = {x["batch_id"]: x["status"] for x in ok(client.get("/api/batches"))}
    assert batches[b["batch_id"]] == "expired"
    sealed = ok(client.post("/api/seal", json={"period_id": "2026-10", "batch_id": b2["batch_id"]}))
    assert sealed["status"] == "sealed" and sealed["revision_count"] == 1


# ---------------------------------------------------------------------------
# 封印闸
# ---------------------------------------------------------------------------
def test_sealed_period_rejects_revision_and_keeps_current(client):
    before = current(client)["M-W-102"]
    b = align(client)
    ok(client.post("/api/seal", json={"period_id": "2026-10", "batch_id": b["batch_id"]}))

    r = revise(client, before["estimate_id"], 1.0)
    assert r.status_code == 409 and "封印" in r.json()["detail"]
    after = current(client)["M-W-102"]
    assert after["qty"] == 15.0 and after["version_id"] == before["version_id"]

    # 派生写入与对齐同闸
    assert client.post("/api/estimates", json={
        "meter_id": "M-W-201", "period_id": "2026-10", "qty": 1, "doc_no": "Z"}).status_code == 409
    assert client.post("/api/align", json={"period_id": "2026-10"}).status_code == 409
    # 历史仍可查（只读不被闸挡）
    detail = ok(client.get(f"/api/estimates/{before['estimate_id']}"))
    assert detail["current"]["qty"] == 15.0


def test_seal_without_realign_refused_for_other_open_period(client):
    # 2026-09 无估抄实抄，对齐应 400 而非静默成功
    assert client.post("/api/align", json={"period_id": "2026-09"}).status_code == 400


# ---------------------------------------------------------------------------
# 并发
# ---------------------------------------------------------------------------
def test_concurrent_revisions_leave_exactly_one_current(client):
    import threading

    est = current(client)["M-W-102"]["estimate_id"]
    results: list[int] = []

    def bump(i):
        try:
            r = revise(client, est, 14.0 + i * 0.1, reason=f"并发{i}")
            results.append(r.status_code)
        except Exception:  # pragma: no cover
            results.append(599)

    threads = [threading.Thread(target=bump, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(code == 200 for code in results), results

    detail = ok(client.get(f"/api/estimates/{est}"))
    currents = [v for v in detail["versions"] if v["is_current"]]
    assert len(currents) == 1
    assert currents[0]["version_no"] == 7
    assert detail["revision_count"] == 6
    assert {v["version_no"] for v in detail["versions"]} == set(range(1, 8))


def test_optimistic_version_guard(client):
    est = current(client)["M-W-102"]["estimate_id"]
    ok(revise(client, est, 14.0, expected_version_no=1))
    r = revise(client, est, 13.0, expected_version_no=1)  # 过期期望
    assert r.status_code == 409 and "已被他人修订" in r.json()["detail"]
    assert current(client)["M-W-102"]["qty"] == 14.0


def test_concurrent_align_and_revise_never_leaves_half_state(client):
    """并发对齐与修订交错：任一 open 批若快照非现行，必须被标 stale；
    且不存在“批内估抄=现行，但列表却说现行是另一版本”的撕裂。"""
    import threading

    est = current(client)["M-W-102"]["estimate_id"]
    errors: list = []

    def do_align():
        try:
            align(client)
        except AssertionError as e:  # 400 等情况不计
            errors.append(e)

    def do_revise():
        try:
            revise(client, est, 14.0, reason="并发对齐期修订")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=do_align), threading.Thread(target=do_revise)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors

    cur = current(client)["M-W-102"]
    batches = ok(client.get("/api/batches", params={"period_id": "2026-10"}))
    assert batches, "并发后应至少有一个对账批"
    for b in batches:
        snap = ok(client.get(f"/api/batches/{b['batch_id']}/snapshot"))
        used = snap["estimate_snapshot"]["M-W-102"]
        if used["version_id"] == cur["version_id"]:
            assert snap["stale_meters"] == []
        else:
            assert any(s["meter_id"] == "M-W-102" for s in snap["stale_meters"])
        # 不允许出现既标新鲜又用了旧版本
        assert not (snap["stale_meters"] == [] and used["version_no"] != cur["version_no"])


def test_revision_during_seal_race_is_serialized(client):
    """封印与修订并发：要么修订先到、封印因过期批被拒；要么封印先到、修订被拒。
    绝不能出现“已封印账期里有修订后却仍绑旧批”的撕裂。"""
    import threading

    est = current(client)["M-W-102"]["estimate_id"]
    b = align(client)
    codes: dict[str, int] = {}

    def do_revise():
        codes["revise"] = revise(client, est, 14.0, reason="封印竞态").status_code

    def do_seal():
        codes["seal"] = client.post(
            "/api/seal", json={"period_id": "2026-10", "batch_id": b["batch_id"]}
        ).status_code

    threads = [threading.Thread(target=do_revise), threading.Thread(target=do_seal)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    cur = current(client)["M-W-102"]
    period = next(p for p in ok(client.get("/api/periods")) if p["period_id"] == "2026-10")
    if period["status"] == "sealed":
        # 封印成功则修订必须被拒，现行仍是 v1
        assert codes["revise"] == 409
        assert cur["version_no"] == 1
    else:
        # 封印被拒（过期批），修订成功；重对齐后可封印且快照为 v2
        assert codes["seal"] == 409
        assert cur["version_no"] == 2
        b2 = align(client)
        sealed = ok(client.post("/api/seal", json={"period_id": "2026-10", "batch_id": b2["batch_id"]}))
        assert sealed["status"] == "sealed"
        snap = ok(client.get(f"/api/batches/{b2['batch_id']}/snapshot"))
        assert snap["estimate_snapshot"]["M-W-102"]["version_no"] == 2



# ---------------------------------------------------------------------------
# 总览
# ---------------------------------------------------------------------------
def test_overview_counts_revisions_and_batches(client):
    o = ok(client.get("/api/overview"))
    assert o["total_revisions"] == 0 and o["revised_estimates"] == 0

    est = current(client)["M-W-102"]["estimate_id"]
    ok(revise(client, est, 14.0))
    ok(revise(client, est, 13.0))
    b = align(client)

    o = ok(client.get("/api/overview"))
    assert o["total_revisions"] == 2 and o["revised_estimates"] == 1
    assert o["recent_batches"][0]["batch_id"] == b["batch_id"]
    assert o["recent_batches"][0]["revision_count"] == 2


from fastapi.testclient import TestClient


def test_legacy_single_table_migrates_into_chain(tmp_path, monkeypatch):
    """旧库（单 estimates 表）启动时导入版本链，不丢行、只留一个 current。"""
    import sqlite3

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE estimates (
          estimate_id TEXT PRIMARY KEY, meter_id TEXT NOT NULL, period_id TEXT NOT NULL,
          qty REAL NOT NULL, doc_no TEXT NOT NULL, loaded_at TEXT NOT NULL,
          UNIQUE(meter_id, period_id, doc_no));
        INSERT INTO estimates VALUES ('E1','M-W-101','2026-10',12,'D1','2026-10-01T08:00:00');
        INSERT INTO estimates VALUES ('E2','M-W-101','2026-10',15,'D2','2026-10-02T08:00:00');
        """
    )
    conn.commit()
    conn.close()

    import app.db as appdb
    monkeypatch.setattr(appdb, "DB_PATH", db)
    import app.main as main
    import app.seed as seed
    seed.seed_if_empty()
    with TestClient(main.app) as c:
        rows = ok(c.get("/api/estimates", params={"period_id": "2026-10"}))
        assert len(rows) == 1 and rows[0]["version_no"] == 2 and rows[0]["qty"] == 15
        detail = ok(c.get(f"/api/estimates/{rows[0]['estimate_id']}"))
        assert [v["qty"] for v in detail["versions"]] == [12, 15]
