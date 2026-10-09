# -*- coding: utf-8 -*-
"""[2026-10-09 崩溃循环根因排查] 后端受管启动包装器。

做什么：
  1) 端口守卫：8000 已在 LISTENING 就**不重复启动**（原 .cmd 有此守卫，
     看门狗用的 .vbs 路径没有 ⇒ 慢启动期间被重复拉起、端口互踩）；
  2) 生命周期记录：把后端进程的 启动时间/退出时间/退出码 写进
     logs/backend-lifecycle.log —— 后端"无声死亡"从此有第一手现场
     （退出码能区分 自然退出(0)/Taskkill(1)/TerminateProcess(-1)/崩溃(非零)）。
本文件保持功能极简，只依赖标准库。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "backend-lifecycle.log"
PORT = int(__import__("os").environ.get("BACKEND_PORT", "8000") or 8000)


def _log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " [lifecycle] " + msg
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def _port_in_use(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def main() -> int:
    if _port_in_use(PORT):
        _log(f"skip start: port {PORT} already in use")
        return 0
    t0 = time.time()
    _log(f"starting backend pid={__import__('os').getpid()} port={PORT}")
    child = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "run_uvicorn_dev.py")],
        cwd=str(ROOT),
    )
    _log(f"backend child pid={child.pid}")
    rc = child.wait()
    dt = time.time() - t0
    _log(f"backend exited rc={rc} uptime={dt:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
