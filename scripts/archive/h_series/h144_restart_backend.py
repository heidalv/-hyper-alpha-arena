# -*- coding: utf-8 -*-
"""[H144 2026-09-21] 重启后端，让 `hft_routes.py` 的手续费改动生效。

# 为什么要重启

后端是 `NO_RELOAD=true` 启动的（02:53:57），**热重载关闭** ⇒ 磁盘上改了代码，
运行中的进程不会加载。实证：用 TestClient 直接跑新代码，`/api/hft/account`
返回 `fee_usd = -51.2589`；而打真实的 8000 端口，同一个接口**没有 fee 字段**。
⇒ 这正是用户报的「没有手续费数据」在服务侧的成因。

# 安全性

  · **MM 车道在独立进程**（`mm_lane_worker.py`，由计划任务 `DSH_MM_WORKER` 拉起）
    ⇒ 重启后端**不影响**车道 tick / 账本写入 / 持仓。
  · 影响面：HTTP 接口在重启期间（约 30~60s）不可用；前端页面短暂打不开。
  · 回滚：无需（重启不改变任何配置）。

# 做法

按仓库既有的 `scripts/spawn_backend.ps1` 启动（同一套 env：BACKEND_PORT=8000、
NO_RELOAD=true、DATA_CENTER_MODE=standalone），保证与现网一致。

用法：
    .venv\\Scripts\\python.exe scripts\\h144_restart_backend.py
    .venv\\Scripts\\python.exe scripts\\h144_restart_backend.py --apply
"""
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEALTH = "http://127.0.0.1:8000/api/hft/ping"
ACCOUNT = "http://127.0.0.1:8000/api/hft/account"


def _procs() -> list:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*run_uvicorn_dev*' } | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=60).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def _probe(url: str, timeout: float = 5.0):
    """返回 (status_code, json_or_None)。**失败时 status_code 为 None**。

    ⚠️ 曾经写成失败时把异常类名塞进第二个位置 ⇒ 调用方 `js.get(...)` 抛
    `AttributeError: 'str' object has no attribute 'get'`，把重启脚本打断在
    「已 kill、未 start」的中间态（后端停机）。**探测函数的返回类型必须恒定**。
    """
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return r.status_code, None
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, None
    except Exception:
        return None, None


def _paper_of(js) -> dict:
    """安全取 js['paper']：js 不是 dict 时返回空 dict（不抛异常）。"""
    if isinstance(js, dict):
        p = js.get("paper")
        if isinstance(p, dict):
            return p
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    print("=" * 92)
    print("H144  重启后端（激活 hft_routes 手续费字段）")
    print("=" * 92)

    before = _procs()
    print(f"\n  当前 uvicorn 进程: {before}")
    st, js = _probe(ACCOUNT)
    has_fee = "fee_usd" in _paper_of(js)
    print(f"  重启前 /api/hft/account: status={st}  paper.fee_usd 字段={'有' if has_fee else '**无**'}")

    # MM worker 是否在跑（重启后端不应影响它，这里只做记录）
    w = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*mm_lane_worker*' }).ProcessId -join ','"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    print(f"  MM worker 进程（独立、不受影响）: {w or '(无)'}")

    if not a.apply:
        print("\n  （预览）加 --apply 执行重启")
        return 0

    print("\n  [1/4] 停止 uvicorn …")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*run_uvicorn_dev*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True, text=True, timeout=60)
    time.sleep(5)
    print(f"        剩余: {_procs()}")

    print("  [2/4] 用仓库既有脚本启动 …")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "scripts" / "spawn_backend.ps1")],
        capture_output=True, text=True, timeout=90)
    print(f"        {r.stdout.strip()[:120]}")

    print("  [3/4] 等待健康 …")
    ok = False
    for i in range(30):
        time.sleep(4)
        st, _ = _probe(HEALTH, timeout=4)
        if st == 200:
            ok = True
            print(f"        第 {i+1} 次探测：/api/hft/ping 200 ✓（{4*(i+1)}s）")
            break
        print(f"        第 {i+1} 次探测：{st}")
    if not ok:
        print("        ✗ 未能在 120s 内健康，请查 logs/backend.error.log")
        return 1

    print("  [4/4] 核对手续费字段 …")
    st, js = _probe(ACCOUNT, timeout=15)
    paper = _paper_of(js)
    print(f"        status={st}")
    for k in ("total_equity", "total_equity_source", "available_balance",
              "realized_usd", "unrealized_usd", "fee_usd", "fee_fills", "fee_gross_usd"):
        print(f"          paper.{k:<22} {paper.get(k)}")
    ok2 = paper.get("fee_usd") is not None
    print(f"\n  ⇒ 手续费字段{'已生效 ✓' if ok2 else '**仍未生效**（请查日志）'}")
    return 0 if ok2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
