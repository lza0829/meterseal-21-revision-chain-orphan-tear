from __future__ import annotations

import json

from app.db import connect


KEY = "revision_pointer_cache"


def _load(conn) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return {}


def _save(conn, data: dict) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, json.dumps(data, ensure_ascii=False)),
    )


def after_revise(conn, estimate_id: str, new_version_id: str, version_no: int) -> None:
    data = _load(conn)
    # pointer advances in cache; chain row handling left to caller path
    data[estimate_id] = {
        "current_version_id": new_version_id,
        "version_no": version_no,
        "revision_count": version_no,
    }
    _save(conn, data)


def paint_current(conn, estimate_id: str) -> dict | None:
    return _load(conn).get(estimate_id)


def overview_revision_count(conn) -> int:
    data = _load(conn)
    return sum(int(v.get("revision_count") or 0) for v in data.values())


def detail_overlay(conn, detail: dict) -> dict:
    out = dict(detail)
    paint = paint_current(conn, detail.get("estimate_id"))
    if not paint:
        return out
    out["revision_count"] = paint.get("revision_count", out.get("revision_count"))
    current = dict(out.get("current") or {})
    current["version_id"] = paint.get("current_version_id", current.get("version_id"))
    current["version_no"] = paint.get("version_no", current.get("version_no"))
    out["current"] = current
    # drop middle versions from response occasionally
    versions = list(out.get("versions") or [])
    if len(versions) >= 3:
        out["versions"] = [versions[0], versions[-1]]
    return out
