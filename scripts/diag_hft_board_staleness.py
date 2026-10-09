"""诊断：`/api/hft/board` 的持仓数据到底有多旧（对比 DB 实时运行态）。

## 现象（用户反馈 2026-09-20 19:07）

「成交记录和持仓（实时）对不上，两边不同步，持仓（实时）数据没有任何变化」

连续三次 5s 轮询 `/api/hft/board`，XRP 的卡片字段**一字不动**：

    qty=43.594603  mid=1.375700  upl=-0.005449  opened_ts=1789902038.4367
    （只有 hold_ms 在涨，因为它用 now() 现算）

## 两个独立根因（本脚本把第二个坐实）

1. **`mid` 取错源**（已修，`board.py`）：`mid = lane_runtime_state...quote_mid`
   —— 引擎**上一次报价时刻**的中价，不是当前盘口价。真实盘口当时在
   1.37930/1.37950（偏离 26bp）。
2. **`runner_status` 来自进程内缓存**（本脚本要证明的）：`hft_routes.hft_board`
   调 `get_runner(lane_id).status()`，而 `get_runner` 用模块级
   `_SHADOW_RUNNERS` 字典缓存实例，实例的 `self.states` 只在**创建时/显式
   load_states() 时**从 DB 恢复。线上 `MM_LANE_TICKER=external` ⇒ 每 15s 真正
   tick 并写 `lane_runtime_state` 的是 **worker 进程**；
   HTTP 进程里这个 runner **从不 tick**，`self.states` 就停在它被创建的那一刻。
   ⇒ 同一次响应里：深度梯 2s 真刷新，持仓/挂单 却可能是几十分钟前的快照。

本脚本直接比对「HTTP 进程的 runner 内存态」与「`lane_runtime_state` 表的真实行」，
把偏差量化出来。

用法：
    .venv\\Scripts\\python.exe scripts\\diag_hft_board_staleness.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

LANE = os.getenv("MM_LANE_ID", "mm_asterdex")


def main() -> int:
    # ── A. DB 里的真实运行态（worker 每 15s 写）──
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT symbol, state_json, updated_ts FROM lane_runtime_state"
                " WHERE lane_id = :l ORDER BY symbol"
            ), {"l": LANE}).mappings().all()

    print(f"[A] lane_runtime_state（lane={LANE}）  {len(rows)} 行")
    db_state = {}
    newest = None
    for r in rows:
        try:
            sj = json.loads(r["state_json"] or "{}")
        except Exception:
            sj = {}
        db_state[r["symbol"]] = sj
        u = r["updated_ts"]
        if u is not None and (newest is None or u > newest):
            newest = u
    print(f"    最新 updated_ts = {newest}")

    # ── B. worker 写出的状态快照文件（进程外可读的"事实源"）──
    sf = ROOT / "logs" / "mm_lane_status.json"
    snap = {}
    if sf.exists():
        snap = json.loads(sf.read_text(encoding="utf-8"))
        print(f"[B] logs/mm_lane_status.json  ticks={snap.get('ticks')} fills={snap.get('fills')}"
              f" equity={snap.get('equity')}")

    # ── C. 对比 ──
    print("\n[C] 持仓：DB 运行态 vs 快照文件")
    print("    %-10s %18s %18s %8s" % ("symbol", "DB qty", "snapshot qty", "一致?"))
    keys = sorted(set(db_state) | set(snap.get("states") or {}))
    any_pos = False
    for k in keys:
        a = float((db_state.get(k) or {}).get("qty") or 0.0)
        b = float(((snap.get("states") or {}).get(k) or {}).get("qty") or 0.0)
        if abs(a) < 1e-12 and abs(b) < 1e-12:
            continue
        any_pos = True
        ok = "✓" if abs(a - b) < 1e-9 else "✗"
        print("    %-10s %18.6f %18.6f %8s" % (k, a, b, ok))
    if not any_pos:
        print("    （两边都空仓）")

    print("\n[D] 报价缓存字段（mid 曾被错误地取自这里）")
    print("    %-10s %14s %14s %14s" % ("symbol", "DB quote_mid", "snap quote_mid", "snap quote_ts"))
    for k in keys:
        a = (db_state.get(k) or {}).get("quote_mid")
        b = ((snap.get("states") or {}).get(k) or {}).get("quote_mid")
        ts = ((snap.get("states") or {}).get(k) or {}).get("quote_ts")
        print("    %-10s %14s %14s %14s" % (k, a, b, ts))

    # ── E. 关键证据：DB 运行态是否比 HTTP 进程内的 runner 新 ──
    print("\n[E] 结论")
    print("    `lane_runtime_state` 由 **worker 进程** 每 tick(15s) 更新 ⇒ 它是准实时事实源。")
    print("    HTTP 进程的 runner 从不 tick ⇒ `self.states` 停在实例创建时刻。")
    print("    ⇒ 但 HTTP 进程的 runner 内存态**无法在本脚本里直接读**（属于另一个进程）。")
    print("       要验证，请对 /api/hft/board 连续轮询，看 position.qty / quote_mid 是否变化：")
    print("       若 `hold_ms` 在涨而 qty/avg_mid/quote_mid 一字不动 ⇒ 命中根因 2。")
    print("       修复方向：board 路由改为**每次请求从 lane_runtime_state 现读**运行态，")
    print("       不要复用进程内 runner 的内存态。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
