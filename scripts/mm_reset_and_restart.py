# -*- coding: utf-8 -*-
"""[F220] 停后端 → 重置车道账户+运行态持仓 → 重启后端（一键、可复现）。

为什么必须停后端：runner 常驻内存里持有每个币的 qty/avg_px，DB 只是它的快照 ✓。
只在后端运行时改 DB，下一次 tick 就把内存旧仓写回来 ✗（这正是上次"重置无效"的原因之一）。
"""
import os
import subprocess
import sys
import time

ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"
PY = os.path.join(ROOT, "backend", ".venv", "Scripts", "python.exe")


def port_free() -> bool:
    import socket
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", 8000))
        s.close()
        return False
    except OSError:
        return True


def kill_backend() -> None:
    if port_free():
        print("[1] 8000 端口空闲，无需停后端")
        return
    import psutil
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr and conn.laddr.port == 8000 and conn.status == "LISTEN":
            try:
                p = psutil.Process(conn.pid)
                print(f"[1] kill backend pid={conn.pid} ({p.name()})")
                p.kill()
            except Exception as e:
                print(f"[1] kill {conn.pid} err: {e}")
    for _ in range(30):
        if port_free():
            print("[1] 8000 已释放")
            return
        time.sleep(1)
    print("[1] ⚠ 8000 仍未释放")


def run(script: str, *args: str) -> int:
    cmd = [PY, os.path.join(ROOT, "scripts", script), *args]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    print(f"[run] {' '.join(cmd[1:])}")
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    print(out.rstrip())
    if r.returncode != 0:
        print(err.rstrip()[-4000:])
    print(f"[run] exit={r.returncode}")
    return r.returncode


if __name__ == "__main__":
    lane = sys.argv[1] if len(sys.argv) > 1 else "mm_asterdex"
    kill_backend()
    rc = run("mm_reset_lane.py", lane)
    if rc != 0:
        print("✗ 重置失败，**不重启后端**（避免带着坏状态继续跑）")
        raise SystemExit(rc)
    rc2 = run("_restart_backend_clean.py")
    raise SystemExit(rc2)
