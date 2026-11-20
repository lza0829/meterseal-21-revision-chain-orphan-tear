from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "抄表簿"
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
# 允许测试/部署用环境变量指向独立库文件
DB_PATH = Path(os.environ.get("METERSEAL_DB", str(DATA_DIR / "meterseal.db")))
