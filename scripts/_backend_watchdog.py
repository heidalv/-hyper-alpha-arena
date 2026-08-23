# -*- coding: utf-8 -*-
"""Backend watchdog（2026-08-23）：对抗 DSH web 平台对 8000 端口的健康监管重启。

背景：DSH web 平台监控 8000 端口，响应慢即杀进程重启（其重启有时失败，
导致交易后端真空）。本 watchdog 以独立进程运行：
- 每 15s 探测 /api/health；
- 连续 2 次失败 → BREAKAWAY 拉起 backend（run_uvicorn_dev.py）；
- 日志写 logs/backend_watchdog.log。

用法: backend\.venv\Scripts\python.exe scripts\_backend_watchdog.py
"""
import os
import subprocess
import sys
import time
import urllib.request

ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
PY = os.path.join(ROOT, "backend", ".venv", "Scripts", "python.exe")
BREAKAWAY = 0x00000008 | 0x00000200 | 0x00000080  # BREAKAWAY | NEW_GROUP | NO_WINDOW

_logf = open(os.path.join(ROOT, "logs", "backend_watchdog.log"), "a", encoding="utf-8", buffering=1)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [watchdog] {msg}"
    print(line, flush=True)
    _logf.write(line + "\n")
    _logf.flush()


def healthy() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def spawn_backend() -> None:
    env = dict(os.environ)
    env.update({
        "BACKEND_PORT": "8000",
        "BACKEND_HOST": "0.0.0.0",
        "NO_RELOAD": "true",
        "DATA_CENTER_MODE": "standalone",
        "PYTHONUNBUFFERED": "1",
    })
    out = open(os.path.join(ROOT, "logs", "backend.log"), "a", encoding="utf-8", buffering=1)
    err = open(os.path.join(ROOT, "logs", "backend.error.log"), "a", encoding="utf-8", buffering=1)
    p = subprocess.Popen(
        [PY, os.path.join(ROOT, "scripts", "run_uvicorn_dev.py")],
        cwd=ROOT,
        env=env,
        stdout=out,
        stderr=err,
        creationflags=BREAKAWAY,
        close_fds=True,
    )
    log(f"backend spawned pid={p.pid}")


def main() -> None:
    log("watchdog started")
    fail_streak = 0
    last_spawn = 0.0
    while True:
        ok = healthy()
        if ok:
            if fail_streak:
                log("backend healthy again")
            fail_streak = 0
        else:
            fail_streak += 1
            log(f"backend down (streak={fail_streak})")
            if fail_streak >= 2 and time.time() - last_spawn > 60:
                # 清理可能残留的 8000 占用者
                try:
                    import psutil
                    for conn in psutil.net_connections(kind="tcp"):
                        if conn.laddr and conn.laddr.port == 8000 and conn.status == "LISTEN":
                            try:
                                psutil.Process(conn.pid).kill()
                                log(f"killed stale pid={conn.pid}")
                            except Exception as e:
                                log(f"kill {conn.pid} err: {e}")
                except Exception:
                    pass
                time.sleep(3)
                spawn_backend()
                last_spawn = time.time()
                # 给启动留 60s 宽限
                for _ in range(20):
                    time.sleep(3)
                    if healthy():
                        break
                fail_streak = 0
        time.sleep(15)


if __name__ == "__main__":
    main()
