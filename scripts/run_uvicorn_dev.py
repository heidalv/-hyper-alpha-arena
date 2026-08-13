#!/usr/bin/env python3
"""Dev backend launcher — avoids Windows cmd/PowerShell glob expansion on uvicorn reload flags."""
from __future__ import annotations

import os
import sys

# [2026-07-11 修复 - 原生线程无限增长根因] 必须在任何 numpy/torch/pandas 等 BLAS/OpenMP
# 依赖库被 import 之前设置这些环境变量，否则不生效。
# 实测现象：QAA_EMBEDDING_BACKEND=neural(sentence-transformers/PyTorch CPU 推理) 打开时，
# 进程 OS 线程数以 ~20/分钟 速度无限增长(半小时破450)，关掉 neural 后线程数稳定不再增长
# ——AB 对比在同一晚验证过。根因是本项目是"单进程 + 数十个常驻后台线程/大量临时线程"
# 的架构，torch/numpy 底层 OpenMP(libiomp)/MKL 线程池在被"不同的调用方 OS 线程"反复触发
# CPU 推理(每次 RAG embed 调用可能来自不同的后台线程)时，会按线程上下文重新创建线程组，
# 且这些线程组不会主动退出，导致原生线程只增不减——这不是 Python 层 threading 模块能看到
# 或控制的(threading.enumerate() 一直只有几十个，OS 线程却几百个)。
# 标准修复：把 BLAS/OpenMP 后端强制限制为单线程，不让它在推理时自建线程池
# (Python 层已经有充分的多线程并行，不需要 BLAS 再嵌套开线程)。
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "TOKENIZERS_PARALLELISM"):
    os.environ.setdefault(_v, "1" if _v != "TOKENIZERS_PARALLELISM" else "false")
try:
    import torch  # noqa: E402 — 尽早导入以便在任何模型加载前锁定线程数为 1
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
except Exception:
    pass  # torch 未安装/导入失败不影响主流程，上面的环境变量仍然生效

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.join(_REPO_ROOT, "backend")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# [fix] backend/ 内部代码使用非限定导入 (from database.models, from factors, ...)，
# 因此需要将 backend/ 追加到 sys.path 末尾（非插入，避免与 backend/models.py 存根冲突）。
if _BACKEND_DIR not in sys.path:
    sys.path.append(_BACKEND_DIR)
os.chdir(_REPO_ROOT)


import uvicorn  # noqa: E402


def main() -> None:
    # [2026-07-11 修复] 本项目是单进程 FastAPI + 大量后台线程(APScheduler任务/
    # ThreadPoolExecutor并行市场扫描/LLM流式调用) 混合架构，所有线程共享同一个 GIL。
    # 默认 GIL 切换间隔 5ms，一旦某个CPU密集的后台线程连续抢占(如8线程并行做技术
    # 指标计算)，处理HTTP请求的事件循环线程可能几十甚至上百次切换都排不上号，
    # 表现为"简单查询接口卡顿数秒到十几秒"——这正是用户反馈"数据库连接越来越慢/
    # 卡死"的根因之一(另一个是 ai_decision_service 的线程泄漏，已单独修复)。
    # 调小切换间隔让解释器更频繁地在线程间切换，牺牲一点吞吐换取事件循环线程
    # 更及时被调度，对本项目这种"要求响应及时性 > 极限吞吐"的交易系统更合适。
    sys.setswitchinterval(0.001)

    port = int(os.getenv("BACKEND_PORT", "8000"))
    # [2026-08-05 远程访问] 后端监听地址可由 BACKEND_HOST 环境变量控制：
    #   - 0.0.0.0    监听所有网卡（Tailscale 虚拟网卡 + 局域网都能访问，远程部署用）
    #   - 127.0.0.1  仅本机（默认安全，防局域网误连）
    # start-dev.ps1 启动后端时注入 BACKEND_HOST=0.0.0.0，即可让外地电脑通过
    # Tailscale 虚拟 IP (100.x.x.x) 访问本项目。
    host = os.getenv("BACKEND_HOST", "127.0.0.1")
    # 默认开热重载；NO_RELOAD=1/true 时关闭（start-backend-fix.cmd / start-dev -NoReload）
    no_reload = os.getenv("NO_RELOAD", "").strip().lower() in ("1", "true", "yes", "on")
    backend_dir = os.path.join(_REPO_ROOT, "backend")

    # reload 子进程用 multiprocessing.spawn，不跑本文件顶部的 sys.path 注入。
    # 把仓库根 + backend 写入 PYTHONPATH，避免 worker 缺包 / 找不到 backend.*。
    _pp_parts = [_REPO_ROOT, backend_dir]
    _existing_pp = os.environ.get("PYTHONPATH", "")
    if _existing_pp:
        _pp_parts.extend(p for p in _existing_pp.split(os.pathsep) if p)
    # 去重保序
    _seen: set[str] = set()
    _pp_clean: list[str] = []
    for _p in _pp_parts:
        if _p and _p not in _seen:
            _seen.add(_p)
            _pp_clean.append(_p)
    os.environ["PYTHONPATH"] = os.pathsep.join(_pp_clean)

    # Windows venv：保证 spawn 子进程仍走本仓库 .venv（Scripts 优先 + VIRTUAL_ENV）
    _venv = os.path.join(_REPO_ROOT, ".venv")
    _venv_scripts = os.path.join(_venv, "Scripts" if os.name == "nt" else "bin")
    if os.path.isdir(_venv_scripts):
        os.environ.setdefault("VIRTUAL_ENV", _venv)
        _path0 = os.environ.get("PATH", "")
        if not _path0.lower().startswith(_venv_scripts.lower()):
            os.environ["PATH"] = _venv_scripts + os.pathsep + _path0
    try:
        import multiprocessing as _mp
        _mp.set_executable(sys.executable)
    except Exception:
        pass

    kwargs: dict = {
        "app": "backend.main:app",
        "host": host,
        "port": port,
        "log_level": "info",
        "timeout_graceful_shutdown": 8,
    }
    if not no_reload:
        kwargs.update(
            {
                "reload": True,
                "reload_dirs": [backend_dir],
                "reload_includes": ["*.py"],
                "reload_delay": 8.0,
                "reload_excludes": [
                    "backend/static/*",
                    "backend/data/*",
                    "backend/**/_reload*",
                    "**/__pycache__/*",
                    "**/*.jsonl",
                    "**/*.log",
                    "**/*.db",
                    "**/*.lock",
                    "**/*.pyc",
                ],
            }
        )
        print(
            f"[run_uvicorn_dev] hot-reload ON  host={host} port={port}  "
            f"watch={backend_dir}",
            flush=True,
        )
    else:
        print(
            f"[run_uvicorn_dev] hot-reload OFF (NO_RELOAD)  host={host} port={port}",
            flush=True,
        )

    uvicorn.run(**kwargs)


if __name__ == "__main__":
    main()
