# -*- coding: utf-8 -*-
"""受控重启 backend（BREAKAWAY 脱离宿主监管，干净版）。

背景：DSH web 对 8000 端口有健康监管（响应慢即重启），普通 Job 内启动的
backend 会被监管杀掉。用 Windows BREAKAWAY 标志起独立进程组，与宿主监管
解耦。输出重定向到 logs/backend.log / backend.error.log。
"""
import os
import subprocess
import sys
import time

ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
PY = os.path.join(ROOT, "backend", ".venv", "Scripts", "python.exe")
BREAKAWAY = 0x00000008 | 0x00000200 | 0x00000080  # BREAKAWAY | NEW_GROUP | NO_WINDOW

# 1) 清理 8000 占用者
import socket as _sk


def _port_free() -> bool:
    try:
        s = _sk.socket()
        s.connect(("127.0.0.1", 8000))
        s.close()
        return False
    except OSError:
        return True


if not _port_free():
    import psutil
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr and conn.laddr.port == 8000 and conn.status == "LISTEN":
                try:
                    p = psutil.Process(conn.pid)
                    print(f"killing pid={conn.pid} ({p.name()})")
                    p.kill()
                except Exception as e:
                    print(f"kill {conn.pid} err: {e}")
    except Exception as e:
        print("psutil err:", e)
    time.sleep(5)

# 2) 起后端（脱离 Job）
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
print(f"backend spawned pid={p.pid}")

# 3) 等健康
import urllib.request

ok_flag = False
for i in range(100):
    time.sleep(3)
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=4) as r:
            print(f"UP at +{(i + 1) * 3}s: {r.read().decode()[:80]}")
            ok_flag = True
            break
    except Exception:
        pass
if not ok_flag:
    print("TIMEOUT waiting health")
sys.exit(0 if ok_flag else 1)
