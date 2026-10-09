# -*- coding: utf-8 -*-
"""H157：列出引擎里全部可调参数与当前生效值，按作用分组，标出"调过 / 没调过"。

目的：现在「价差捕获 ≈ 被穿」打平，需要找下一个能撬动的开关。
把参数按作用域分组并标注哪些是**从未调过**的（默认值），那些就是待挖掘的候选。

用法：
    .venv\\Scripts\\python.exe scripts\\h157_param_inventory.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GROUPS = {
    "库存/趋势偏斜（让报价随持仓/走势偏移）": (
        "k_inv", "k_vol", "k_vol_sigma_cap", "k_trend"),
    "挂单宽度": (
        "w_base_bp", "min_width_bp", "max_width_bp", "spread_mult",
        "spread_mult_reduce", "spread_cross_margin", "min_edge_frac"),
    "毒性流闸（OFI）": (
        "ofi_block_threshold", "ofi_flatten_threshold", "ofi_flatten_min_age_ratio"),
    "趋势闸": (
        "trend_pause_bp", "side_trend_min_bp", "side_mode", "trend_lookback"),
    "波动闸": (
        "vol_pause_sigma", "vol_pause_mult", "vol_window"),
    "冻结闸": (
        "frozen_lookback", "frozen_max_move_bp", "frozen_width_bp"),
    "持仓窗口 / 止损": (
        "max_one_side_seconds", "min_hold_seconds", "stop_loss_bp",
        "stop_loss_vol_min", "stop_loss_fast_mult", "stop_maker_grace_sec"),
    "仓位 / 敞口": (
        "compound_ratio", "fill_notional", "max_net_directional_ratio",
        "max_net_exposure_ratio", "max_gross_notional_ratio",
        "max_symbol_notional_ratio", "daily_loss_stop_pct"),
}

# 本轮（2026-09-21）动过的键 —— 用于标出"从未调过"的候选
TUNED = {
    "spread_mult", "compound_ratio", "stop_loss_bp", "stop_loss_vol_min",
    "stop_loss_fast_mult", "max_one_side_seconds", "min_hold_seconds",
    "max_net_directional_ratio", "max_net_exposure_ratio",
    "max_gross_notional_ratio", "daily_loss_stop_pct", "timeout_exit_maker_only",
}


def main() -> int:
    st = Path(ROOT / "logs" / "mm_lane_status.json")
    j = json.loads(st.read_text(encoding="utf-8"))
    p = j.get("params") or {}
    l = j.get("limits") or {}

    print("=" * 96)
    print("H157  引擎参数清单（当前生效值）")
    print("=" * 96)
    never = []
    for g, keys in GROUPS.items():
        print(f"\n  【{g}】")
        for k in keys:
            v = p.get(k, l.get(k, None))
            if v is None:
                print(f"      {k:<28} (心跳未回显)")
                continue
            tag = "（本轮调过）" if k in TUNED else "**从未调过**"
            if k not in TUNED:
                never.append(k)
            print(f"      {k:<28} {str(v):<14}{tag}")

    print(f"\n  {'='*92}")
    print(f"  ⇒ **从未调过**的参数（{len(never)} 个）—— 待挖掘的候选：")
    for k in never:
        print(f"      {k}")
    print(f"\n  判据提示：现在「价差捕获 ≈ 被穿」打平（H142 逐小时：13:00 价差 +0.327bp / "
          f"行情 −0.124bp），\n  说明**静态挂宽**这条路的边际已经用尽；下一步应转向"
          f"**动态调整**（偏斜 / 毒性流 / 趋势）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
