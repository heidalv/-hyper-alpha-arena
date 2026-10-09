# -*- coding: utf-8 -*-
"""Full project restart (app stack only; never touch Postgres).

Stops then starts:
  - backend (run_uvicorn_dev / port 8000)
  - data center (market_data_center)
  - mm_lane_worker
  - frontend next (port 5273) if previously running
  - aster_ws_ingest (if present)

Usage:
  .venv\\Scripts\\python.exe scripts\\restart_full_stack.py
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
LOG = ROOT / "logs" / "full_stack_restart.log"


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ps(cmd: str) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
    )
    return (r.stdout or "") + (r.stderr or "")


def list_match(pattern: str) -> list[int]:
    out = ps(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='node.exe'\" | "
        f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    return [int(x) for x in out.split() if x.strip().isdigit()]


def kill_match(pattern: str, label: str) -> None:
    pids = list_match(pattern)
    if not pids:
        log(f"stop {label}: none")
        return
    # Kill chain roots only when possible: kill all matched (launcher+child)
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
    time.sleep(1.0)
    left = list_match(pattern)
    log(f"stop {label}: killed {pids}; left={left}")


def start_hidden(exe: str, args: list[str], label: str, cwd: Path | None = None) -> None:
    cwd = cwd or ROOT
    # Use Start-Process -WindowStyle Hidden
    arg_s = " ".join('"{0}"'.format(a.replace('"', '`"')) for a in args)
    cmd = (
        f'Start-Process -FilePath "{exe}" -ArgumentList {arg_s} '
        f'-WorkingDirectory "{cwd}" -WindowStyle Hidden'
    )
    # ArgumentList with multiple args is tricky in PS; use one string form:
    joined = subprocess.list2cmdline([exe] + args)
    # Prefer wscript quiet launcher for python
    if exe.lower().endswith("python.exe"):
        vbs = ROOT / "scripts" / "run-quiet.vbs"
        subprocess.run(
            ["wscript.exe", "//B", "//Nologo", str(vbs), exe] + args,
            cwd=str(cwd),
            capture_output=True,
        )
        log(f"start {label}: quiet {args[0] if args else exe}")
        return
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f'Start-Process -FilePath "{exe}" -ArgumentList @({",".join(chr(39)+a+chr(39) for a in args)}) '
         f'-WorkingDirectory "{cwd}" -WindowStyle Hidden'],
        capture_output=True,
    )
    log(f"start {label}: {joined}")


def wait_http(url: str, timeout: float = 90.0) -> bool:
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if 200 <= int(r.status) < 500:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def main() -> int:
    log("=" * 60)
    log("FULL STACK RESTART begin")

    # 1) Stop app processes (never postgres)
    kill_match("mm_lane_worker", "mm_worker")
    kill_match("run_uvicorn_dev|start_server\\.py|uvicorn", "backend")
    kill_match("market_data_center", "data_center")
    # frontend next on 5273
    kill_match("next\\\\dist\\\\bin\\\\next|next-server|frontend-next", "frontend")
    # optional ingest / stream – restart if they were part of stack
    kill_match("aster_ws_ingest", "aster_ingest")
    # clear mm locks
    for name in ("mm_lane_worker.lock", "mm_lane_worker.lock.pid"):
        p = ROOT / "logs" / name
        try:
            if p.exists():
                p.unlink()
                log(f"removed {name}")
        except Exception as e:
            log(f"lock rm {name}: {e}")

    time.sleep(2)

    # 2) Start data center first (backend depends on DB feeds)
    start_hidden(PY, ["-m", "backend.workers.market_data_center"], "data_center")
    time.sleep(3)

    # 3) Backend via official noreload path (quiet)
    # start-backend-noreload.cmd uses console; launch python directly with same env
    env_cmd = (
        f'$env:NO_RELOAD="true"; $env:DATA_CENTER_MODE="standalone"; '
        f'$env:BACKEND_PORT="8000"; $env:BACKEND_HOST="0.0.0.0"; '
        f'$env:PYTHONIOENCODING="utf-8"; '
        f'Start-Process -FilePath "{PY}" -ArgumentList "scripts\\run_uvicorn_dev.py" '
        f'-WorkingDirectory "{ROOT}" -WindowStyle Hidden '
        f'-RedirectStandardOutput "{ROOT}\\logs\\backend.log" '
        f'-RedirectStandardError "{ROOT}\\logs\\backend.error.log"'
    )
    # RedirectStandardOutput can't append easily; use quiet vbs without redirect —
    # run_uvicorn already logs to backend.pid*.log via app. Prefer quiet:
    start_hidden(PY, ["scripts\\run_uvicorn_dev.py"], "backend")

    # 4) MM worker via schtasks (official)
    subprocess.run(["schtasks", "/Run", "/TN", "DSH_MM_WORKER"], capture_output=True)
    log("start mm_worker: schtasks DSH_MM_WORKER")

    # 5) Frontend if port free – start quiet
    start_hidden(
        "powershell.exe",
        ["-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "scripts" / "start-frontend-guarded-quiet.ps1")],
        "frontend",
    )

    # 6) Aster ingest – only if script exists (path may be outside)
    ingest = Path(r"D:\001Alpha\research_l1\services\aster_ws_ingest.py")
    if ingest.exists():
        # Keep previous symbol set minimal restart: use schtasks if any; else skip symbols complexity
        # Re-detect previous cmdline from nothing – start with a known default via depth watchdog task
        subprocess.run(
            ["schtasks", "/Run", "/TN", "DSH_ASTER_DEPTH_WATCHDOG"],
            capture_output=True,
        )
        log("start aster: schtasks DSH_ASTER_DEPTH_WATCHDOG")

    log("waiting backend health...")
    ok = wait_http("http://127.0.0.1:8000/api/config/default-exchange", 120)
    log(f"backend health={'OK' if ok else 'FAIL'}")

    # wait mm heartbeat
    status = ROOT / "logs" / "mm_lane_status.json"
    mm_ok = False
    for _ in range(45):
        if status.exists() and (time.time() - status.stat().st_mtime) < 30:
            mm_ok = True
            break
        time.sleep(2)
    log(f"mm heartbeat={'OK' if mm_ok else 'WAIT/FAIL'}")

    # summary
    for label, pat in (
        ("backend", "run_uvicorn_dev"),
        ("data_center", "market_data_center"),
        ("mm_worker", "mm_lane_worker"),
        ("aster", "aster_ws_ingest"),
    ):
        log(f"alive {label}: {list_match(pat)}")

    log("FULL STACK RESTART done")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
