# -*- coding: utf-8 -*-
"""正式重启后端（Job 脱离，免回收）。kill 指定卡死进程后拉起。"""
import os
import subprocess
import sys
import time
import datetime

ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
PY = os.path.join(ROOT, "backend", ".venv", "Scripts", "python.exe")
BREAKAWAY = 0x00000008 | 0x00000200 | 0x00000080  # BREAKAWAY | NEW_GROUP | NO_WINDOW

# 1) 清理：8000 占用者 + 已知卡死进程（29236 管道阻塞的启动链父进程）
def _kill_pid(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15)
        return True
    except Exception:
        return False

_killed = []
for pid in (29236, 29236,):
    if _kill_pid(pid):
        _killed.append(pid)
# 端口 8000 占用者
try:
    import socket
    s = socket.socket()
    s.connect(("127.0.0.1", 8000))
    s.close()
    print("8000 still occupied")
except OSError:
    print("8000 free")

# 2) 起后端
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
print(f"backend spawned pid={p.pid} at {datetime.datetime.now().isoformat()}")
out.flush()

# 3) 等健康
import urllib.request, json
ok_flag = False
for i in range(120):
    time.sleep(3)
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/system/status", timeout=4) as r:
            body = r.read().decode()
            print(f"UP at +{i*3}s: {body}")
            ok_flag = True
            break
    except Exception:
        pass
if not ok_flag:
    print("TIMEOUT waiting health")
sys.exit(0 if ok_flag else 1)
