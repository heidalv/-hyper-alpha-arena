# -*- coding: utf-8 -*-
"""
backend_pulse_monitor — 后端性能脉冲监视器（诊断工具，零业务影响）

职责:
 1) 每 30s 探测 8000（default-exchange, 超时 5s）；
 2) 响应 > 3s 或失败 → 记录日志 + py-spy dump 现场栈到 logs/hang_dump_<ts>.txt；
 3) 每 5 分钟记录进程趋势(cpu/线程/内存)到 logs/backend_pulse.csv —— 验证"平稳"用数据说话。

启动（独立于 SSH 会话）:
  schtasks /create /tn DSH_PULSE_MON /tr "D:\\001Alpha\\Hyper-Alpha-Arena\\.runtime\\Python312\\python.exe D:\\001Alpha\\Hyper-Alpha-Arena\\scripts\\backend_pulse_monitor.py" /sc once /st 00:00 /f
  schtasks /run /tn DSH_PULSE_MON
"""
import csv
import os
import subprocess
import time
import urllib.request

REPO = r"D:\001Alpha\Hyper-Alpha-Arena"
LOG_DIR = os.path.join(REPO, "logs")
BASE = "http://127.0.0.1:8000"
PROBE = BASE + "/api/config/default-exchange"
CSV = os.path.join(LOG_DIR, "backend_pulse.csv")
PULSE_LOG = os.path.join(LOG_DIR, "backend_pulse.log")
PYSPY = os.path.join(REPO, r".venv\Scripts\py-spy.exe")


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " [pulse] " + msg
    with open(PULSE_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def probe(timeout: float = 5.0) -> float:
    t0 = time.time()
    try:
        urllib.request.urlopen(PROBE, timeout=timeout).read(64)
        return time.time() - t0
    except Exception:
        return -1.0


def backend_pid() -> str:
    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()
        for line in out:
            if line.strip().isdigit():
                return line.strip()
    except Exception:
        pass
    return ""


def dump_stack(pid: str) -> None:
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(LOG_DIR, f"hang_dump_{ts}.txt")
    try:
        r = subprocess.run(
            [PYSPY, "dump", "--pid", pid], capture_output=True, text=True, timeout=30,
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write("=== probe ts=%s pid=%s ===\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), pid))
            f.write(r.stdout or "")
            f.write(r.stderr or "")
        log("hang dump saved: " + path)
    except Exception as e:
        log("dump failed: %s" % e)


def main() -> None:
    log("backend pulse monitor started (probe=30s, slow>3s => dump, trend=5min)")
    slow_streak = 0
    last_trend = 0.0
    while True:
        try:
            t = probe()
            pid = backend_pid()
            if t < 0:
                slow_streak += 1
                log("PROBE FAIL (%d consecutive) pid=%s" % (slow_streak, pid or "none"))
                if pid:
                    dump_stack(pid)
            elif t > 3.0:
                slow_streak += 1
                log("SLOW %.2fs (%d consecutive) pid=%s" % (t, slow_streak, pid))
                if pid:
                    dump_stack(pid)
            else:
                if slow_streak:
                    log("recovered (%.2fs)" % t)
                slow_streak = 0

            if time.time() - last_trend >= 300.0 and pid:
                last_trend = time.time()
                try:
                    out = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-Command",
                         "$p=Get-Process -Id %s; $p.CPU.ToString()+','+$p.Threads.Count.ToString()+','+[math]::Round($p.WorkingSet64/1MB).ToString()+','+(Get-Date).ToString('HH:mm:ss')" % pid],
                        capture_output=True, text=True, timeout=10,
                    ).stdout.strip()
                    new = not os.path.exists(CSV)
                    with open(CSV, "a", encoding="utf-8", newline="") as f:
                        w = csv.writer(f)
                        if new:
                            w.writerow(["time", "cpu_cum_s", "threads", "ws_mb"])
                        row = out.split(",")
                        if len(row) == 4:
                            w.writerow([row[3], row[0], row[1], row[2]])
                    log("trend: " + out.replace(",", " "))
                except Exception as e:
                    log("trend fail: %s" % e)
        except Exception as e:
            log("monitor loop error: %s" % e)
        time.sleep(30.0)


if __name__ == "__main__":
    main()
