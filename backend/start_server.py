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

# [2026-08-16 CPU 防护] torch CPU 推理默认吃满全部核心（QAA/RAG 嵌入在无 CUDA
# 降级路径上曾实测 20+ 核 100%）。限制 torch 线程数与 OpenMP，防 CPU 打满。
# run_uvicorn_dev.py 有同样保护；start_server 此前缺失。
os.environ.setdefault("OMP_NUM_THREADS", "4")
try:
    import torch  # noqa: E402
    torch.set_num_threads(4)
except Exception:
    pass


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
