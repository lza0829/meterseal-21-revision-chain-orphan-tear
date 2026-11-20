# 抄表簿

骨架：抄表读数装载 → 估抄/实抄对齐 → 差额处置 → 账期封印。

- 前端 http://127.0.0.1:6010（勿用 6000，浏览器会拦）
- 接口 http://127.0.0.1:11000

## 估抄修订架构：版本链

全局只贯彻一种口径：**保留全版本链**。

| 联动点 | 口径 |
| --- | --- |
| 修订入口 | `POST /api/estimates/{id}/revisions`，必须填原因；插入新版本行 |
| 估抄表 | `estimate_versions`：一条估抄一个 `estimate_id`，`version_no` 递增，唯一索引保证至多一个 `is_current=1` |
| 正式现行读数 | 列表 `GET /api/estimates` 只返回 `is_current=1` |
| 历史修订链 | `GET /api/estimates/{id}` 返回全部版本；`/compare?from_no&to_no` 做版本对照 |
| 对齐输入聚合 | `run_align` 只读 `is_current=1`，同表同账期多份实抄取最后装载的一份 |
| 差额分桶 | 差额行记录 `estimate_id / estimate_version_id / estimate_version_no` |
| 账期封印闸 | 已 `sealed`：装载、修订、实抄、对齐、改命运一律 409 |
| 批次快照 | `reconcile_batches.estimate_snapshot`（每表明细版本号）+ `revision_count`；指纹含版本号 |
| 陈旧对齐拦截 | 对齐后又有修订，旧批标 `stale`，封印被拒，须重对齐 |
| 总览修订次数 | `Σ(现行 version_no − 1)`，另给「有修订的估抄」条数 |
| 并发 | `BEGIN IMMEDIATE` 单事务 + 唯一索引 + 可选 `expected_version_no` 乐观闸 |

禁止静默改数而无版本或审计：修订不写原因直接 422。

## 启动

```powershell
cd backend
C:\Users\18435\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 11000 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 6010
```

## 测试

```bash
cd backend
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

覆盖：修订后对齐吃新值、旧版可查、已封账期拒绝修订、并发修订只留一个 current、
陈旧批封印被拒、旧库单表迁移入版本链。
