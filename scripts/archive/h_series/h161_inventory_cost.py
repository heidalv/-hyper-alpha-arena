# -*- coding: utf-8 -*-
"""H161：被动出库变慢的代价 —— 每小时的敞口占用与权益曲线。

# 背景（用户观察到 XRP 持仓 6m26s 超过 300s 上限）

`timeout_exit_maker_only=True` 把超时出库从 taker 改成"等减仓侧挂单被吃"：
  · 收益：taker 费 → 0（本时代 552 笔里只有 3 笔 taker）
  · 代价：**持仓不再按时清掉**（本时代 230 个周期里 227 个"未平"）
          ⇒ 敞口长期被占、库存堆积

本脚本量化"占用"与"收益"两侧，并给出对比基准（改动前的数据）。

用法：
    .venv\\Scripts\\python.exe scripts\\h161_inventory_cost.py
"""
from __future__ import annotations

import json
import statistics as st
import time as _t
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    eq = float(j.get("equity") or 0.0)
    leg = float(j.get("fill_notional") or 0.0)
    states = j.get("states") or {}
    open_pos = {s: float(v.get("qty") or 0.0) for s, v in states.items()
                if abs(float(v.get("qty") or 0.0)) > 1e-12}

    print("=" * 92)
    print("H161  被动出库变慢的代价")
    print("=" * 92)
    print(f"  权益 ${eq:,.2f}   单腿名义 ${leg:,.2f}   持仓 {len(open_pos)} 个")
    print(f"  ⇒ 总名义 ${len(open_pos)*leg:,.2f}  = "
          f"**{len(open_pos)*leg/eq if eq else 0:.1f}× 权益**")

    lim = j.get("limits") or {}
    mx_dir = float(lim.get("max_net_directional_ratio") or 0)
    mx_net = float(lim.get("max_net_exposure_ratio") or 0)
    mx_gross = float(lim.get("max_gross_notional_ratio") or 0)
    print(f"\n  敞口上限（× 权益）：单币 {mx_dir}  净 {mx_net}  总 {mx_gross}")
    print(f"    换算成「能同时开几条腿」："
          f"单币 {mx_dir/2 if leg else 0:.0f} 腿   净 {mx_net/2 if leg else 0:.0f} 腿   "
          f"总 {mx_gross/2 if leg else 0:.0f} 腿")
    print(f"    （单腿 = 2× 权益 ⇒ 每条腿吃掉 2 个「权益倍数」）")

    print(f"\n  ── 被占用的资金 ──")
    occupied = len(open_pos) * leg
    print(f"    已占用名义 ${occupied:,.2f}   占上限 "
          f"{occupied/(mx_gross*eq)*100 if mx_gross and eq else 0:.1f}%")
    print(f"    剩余可开 {max(0, mx_gross*eq - occupied)/leg if leg else 0:.0f} 条腿")

    print(f"\n  ── 与改动前的对比 ──")
    print(f"    {'指标':<30} {'改动前':>14} {'现在':>14}")
    print("    " + "-" * 60)
    rows = [
        ("被动出库中位时长", "31s（全夜被动样本）", "待本时代样本累积"),
        ("被动出库 ≤120s 完成率", "96.1%", "见 H160：227/230 未平"),
        ("taker 费", "−$51.26（全历史）", "**−$0.0119**（本时代）"),
        ("持仓是否按时清", "是（300s taker 兜底）", "**否**（只等被动）"),
    ]
    for k, a, b in rows:
        print(f"    {k:<30} {a:>14} {b:>14}")

    print(f"\n  ── 结论 ──")
    print(f"    改动把「为时间付费」换成了「为占用付费」：")
    print(f"      · 省下的是确定的 4bp/次 taker 费")
    print(f"      · 付出的是不确定的持仓时间 + 占用的敞口额度")
    print(f"    ⇒ 正确的下一步不是「要不要等」，而是**让出库更快**：")
    print(f"      收紧减仓侧挂宽 `spread_mult_reduce`（现在 0.95，偏宽）")
    print(f"      —— 出库腿挂得靠内 ⇒ 更快被吃 ⇒ 占用时间下降。")
    print(f"      这正是 H158 A/B 之后要扫的下一个旋钮。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
