#!/usr/bin/env python3
"""Start the backend server (with hot reload in dev mode).

与 scripts/run_uvicorn_dev.py 对齐：从项目根目录加载 backend.main:app，
避免在 backend/ 目录内直接跑 main:app 导致相对导入失败。
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _BACKEND_DIR not in sys.path:
    sys.path.append(_BACKEND_DIR)
os.chdir(_REPO_ROOT)

import uvicorn  # noqa: E402


if __name__ == "__main__":
    # [2026-07-09] 默认关闭热重载，避免缓存被频繁清空导致仪表盘卡顿。
    reload_mode = os.getenv("DEV_MODE", "false").lower() == "true"
    port = int(os.getenv("BACKEND_PORT", "8000"))
    kwargs: dict = {
        "app": "backend.main:app",
        "host": os.getenv("BACKEND_HOST", "0.0.0.0"),
        "port": port,
        "log_level": "info",
        "timeout_graceful_shutdown": 8,
    }
    if reload_mode:
        kwargs.update({
            "reload": True,
            "reload_dirs": [_BACKEND_DIR],
            "reload_includes": ["*.py"],
            "reload_excludes": [
                "backend/static/*",
                "backend/data/*",
                "**/__pycache__/*",
                "**/*.jsonl",
                "**/*.log",
                "**/*.db",
            ],
        })
    uvicorn.run(**kwargs)
