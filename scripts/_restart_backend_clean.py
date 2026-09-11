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
BREAKAWAY = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | CREATE_NO_WINDOW（不弹黑框）

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
# [2026-08-29 日志轮转根治] 此前把 stdout/stderr 重定向到 backend.log/error.log，
# 与进程内 RotatingFileHandler 抢同一文件（重定向句柄无 FILE_SHARE_DELETE）→
# 轮转 rename 永久 PermissionError、error.log 无限膨胀。改写到独立 console 文件。
#
# [§64 修复 2026-09-10] 这两个 console 文件**没有任何轮转**：`log_retention_service`
# 只清理 `*.log.*`（轮转碎片），而 console 是持续追加的活文件。实测
# `backend-console.log` 已达 **540.5MB**（≈24MB/天）。现启动前按阈值改名，
# 命名成 `*.log.<时间戳>` 即自动落入既有保留策略（LOG_RETENTION_DAYS）。
CONSOLE_LOG_MAX_MB = float(os.getenv("BACKEND_CONSOLE_LOG_MAX_MB", "64") or 64)


def _rotate_if_huge(path: str, max_mb: float) -> None:
    """console 文件超过阈值则改名（失败不阻断启动）。"""
    try:
        if not os.path.exists(path):
            return
        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb < max_mb:
            return
        dst = f"{path}.{time.strftime('%Y%m%d_%H%M%S')}"
        os.replace(path, dst)
        print(f"[rotate] {os.path.basename(path)} {size_mb:.1f}MB > {max_mb:.0f}MB -> {os.path.basename(dst)}")
    except Exception as e:
        print(f"[rotate] {path} 轮转失败（不阻断启动）: {e}")


_console_log = os.path.join(ROOT, "logs", "backend-console.log")
_console_err = os.path.join(ROOT, "logs", "backend-console.err.log")
_rotate_if_huge(_console_log, CONSOLE_LOG_MAX_MB)
_rotate_if_huge(_console_err, CONSOLE_LOG_MAX_MB)
out = open(_console_log, "a", encoding="utf-8", buffering=1)
err = open(_console_err, "a", encoding="utf-8", buffering=1)
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
