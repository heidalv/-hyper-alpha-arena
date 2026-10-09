# -*- coding: utf-8 -*-
r"""重启 mm_lane_worker（F338 上线用）。

# 为什么不直接 `Stop-Process -Force` 全部 python.exe

本机上同时跑着 8 条**独立的** python 进程链（每条都是
`.venv\Scripts\python.exe`（104KB 启动器）→ `.runtime\Python312\python.exe`
（真解释器）的父子结构）：

    binance_social / run_uvicorn(后端) / binance worker / aster_ws_ingest
    mm_lane_worker(本目标) / h125_selector / depth_age_probe /
    market_data_center / mlto.brain_subprocess

按名字杀会把后端、数据中心、WS 抽取一起干掉（本会话已误判过一次
"双进程"，根因就是这个启动器结构）。

⇒ **只按命令行匹配 `mm_lane_worker`，并且只杀"链根"**（父进程不在匹配集里），
  以免杀掉父之后再杀子时子已退出而报错。
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TASK = "DSH_MM_WORKER"
MATCH = "mm_lane_worker"
# [h665d] 锁按车道命名(mm_lane_worker_{LANE}.lock):覆盖纸面与实盘两把锁
LOCKS = [ROOT / "logs" / "mm_lane_worker.lock",
         ROOT / "logs" / "mm_lane_worker.lock.pid",
         ROOT / "logs" / "mm_lane_worker_mm_asterdex.lock",
         ROOT / "logs" / "live" / "mm_lane_worker_mm_asterdex_live.lock"]


def procs() -> list[dict]:
    """列 python.exe 的 (pid, ppid, cmdline)。

    用 `Get-CimInstance` 经 `powershell`（PATH 里是 `powershell`，
    **不是** `pwsh` —— 直接调 `pwsh` 会 `FileNotFoundError`）。
    查不到时返回 `[]`，由调用方决定是"没有"还是"读失败"。
    """
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Select-Object ProcessId,ParentProcessId,CommandLine | "
          "ConvertTo-Json -Compress")
    for exe in ("powershell", "pwsh"):
        try:
            r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
        except FileNotFoundError:
            continue
        out = (r.stdout or "").strip()
        if not out:
            return []
        import json
        try:
            d = json.loads(out)
        except Exception:
            return []
        return d if isinstance(d, list) else [d]
    return []


def main() -> int:
    import sys
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只看，不动手")
    a = ap.parse_args()

    allp = procs()
    matched = {p["ProcessId"]: p for p in allp
               if MATCH in (p.get("CommandLine") or "")}
    roots = [pid for pid, p in matched.items()
             if p.get("ParentProcessId") not in matched]
    print("=" * 92)
    print("重启 mm_lane_worker")
    print("=" * 92)
    print(f"  匹配 `{MATCH}` 的进程 = {sorted(matched)}")
    print(f"  其中链根 = {sorted(roots)}"
          f"（链根数 = 逻辑进程数；父子在匹配集里 = 同一条链）")
    if len(roots) != 1:
        print(f"  ⚠️ 链根数 {len(roots)} ≠ 1 ⇒ 可能有多条 worker 在跑，请注意")
    if a.check:
        return 0

    for pid in sorted(matched):
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"],
                       capture_output=True, text=True)
    time.sleep(3)
    left = [p["ProcessId"] for p in procs() if MATCH in (p.get("CommandLine") or "")]
    print(f"  停止后残留 = {left if left else '无 ✓'}")

    for lk in LOCKS:
        if lk.exists():
            lk.unlink()
            print(f"  已删锁 {lk.name}")

    r = subprocess.run(["schtasks", "/Run", "/TN", TASK],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    print(f"  schtasks /Run {TASK} ⇒ rc={r.returncode} "
          f"{(r.stdout or '').strip()}{(r.stderr or '').strip()}")
    print(f"  等待 worker 起心跳（≤90s）…")
    st = ROOT / "logs" / "mm_lane_status.json"
    t0 = time.time()
    while time.time() - t0 < 90:
        try:
            import json
            j = json.loads(st.read_text(encoding="utf-8"))
            ok = j.get("ok")
            lim = dict(j.get("limits") or {})
            if "max_leg_notional_mult" in lim:
                print(f"  ✓ {time.time()-t0:.0f}s：新代码已生效，"
                      f"max_leg_notional_mult = {lim['max_leg_notional_mult']}")
                print(f"    ok={ok} equity={j.get('equity')} "
                      f"symbols={j.get('symbols')}")
                if not ok:
                    print(f"    ⚠️ ok=false reason={j.get('reason')!r}")
                return 0
        except Exception:
            pass
        time.sleep(5)
    print("  ✗ 90s 内状态里仍无 max_leg_notional_mult ⇒ 新代码可能未加载")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
