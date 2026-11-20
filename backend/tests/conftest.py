from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 在导入 app 之前把默认库指到临时目录，避免污染仓库 data/
os.environ.setdefault("METERSEAL_DB", str(Path(tempfile.mkdtemp()) / "boot.db"))
os.environ.setdefault("METERSEAL_SYNC_NORMAL", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """每个用例一个独立库文件。

    DB_PATH 在 connect() 调用时才被 app.db 读取，所以 patch 模块属性即可，
    FastAPI 应用只构建一次，无需每个用例重载。
    """
    import app.db as db
    import app.main as main
    import app.seed as seed

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    seed.seed_if_empty()
    with TestClient(main.app) as c:
        yield c


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
