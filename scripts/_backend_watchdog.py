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
BREAKAWAY = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | CREATE_NO_WINDOW（不弹黑框）

_logf = None
try:
    _logf = open(os.path.join(ROOT, "logs", "backend_watchdog.log"), "a", encoding="utf-8", buffering=1)
except Exception as _log_err:
    # [2026-08-24 M0-H3] DSH 平台以沙箱 ACL 拉起本脚本时，对 logs/ 的写入可能被
    # 拒绝（PermissionError → 脚本秒死 → 后端永远无人拉起）。日志失败不致命：
    # 退化为 stdout-only（重定向由拉起方处理），watchdog 功能照常。
    print(f"[watchdog] 日志文件打开失败(退化为stdout): {_log_err}", flush=True)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} [watchdog] {msg}"
    print(line, flush=True)
    if _logf is not None:
        try:
            _logf.write(line + "\n")
            _logf.flush()
        except Exception:
            pass


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
    log("watchdog started (v2: 不杀端口、60s 判定，与 DSH 平台监管共存)")
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
            # [2026-08-23 双守护互杀修复] 连续 4 次失败（60s）才动作，且
            # **绝不 kill 8000 的 LISTEN 进程**——那可能是 DSH 平台自己刚拉起的
            # backend；互杀是 90 秒重启死循环+黑框的根因。仅在端口确实无 LISTEN
            # 时 spawn（60 秒冷却防重复）。
            if fail_streak >= 4 and time.time() - last_spawn > 60:
                import socket as _sk
                port_busy = False
                try:
                    _s = _sk.socket()
                    _s.settimeout(3)
                    _s.connect(("127.0.0.1", 8000))
                    _s.close()
                    port_busy = True
                except OSError:
                    port_busy = False
                if port_busy:
                    log("端口有进程但 health 失败——交给 DSH 平台处理，本 watchdog 不杀")
                    fail_streak = 0
                else:
                    spawn_backend()
                    last_spawn = time.time()
                    for _ in range(20):
                        time.sleep(3)
                        if healthy():
                            break
                    fail_streak = 0
        time.sleep(15)


if __name__ == "__main__":
    main()
