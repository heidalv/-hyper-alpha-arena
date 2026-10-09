"""h631 — mm_lane_status.json 的可读readout（重启后"为什么不报价"定位用）。

为什么需要：状态文件是**单行 JSON**、4.3KB，`read` 工具按行截断到 2000 字符
⇒ 只能看到前半段（skip_counts 只有前 3 个键、limits 被腰斩）。此前多次
"读数看一半"就是这类截断造成的 ✗。

本脚本按 **诊断顺序** 打印，而不是按字典顺序：
  1) 活性：ts / ticks / fills / last_tick_ts / equity
  2) 报价行为：quoted_decisions / side_counts / sigma_decisions
  3) 门禁：skip_counts（全量，降序）+ gate_probe_counts（白名单是否放行）
  4) 参数/限额：只打与"不报价"可能相关的键
  5) 每币状态：qty / 挂单价 / 挂单时刻

用法：
    python scripts/h631_status_readout.py            # 全部
    python scripts/h631_status_readout.py --gates    # 只看门禁
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "logs" / "mm_lane_status.json"

# 与"不报价"直接相关的限额键（其余是配置噪音）
LIMIT_KEYS = (
    # [h631] 三把"把决策全拦掉"的闸门（重启后实测：jump_pause/trend_add_block/
    # entry_block 三者合计 = 全部决策数以 100%）⇒ 它们的阈值必须可见
    "jump_pause_bp", "jump_pause_sec", "jump_pause_symbols",
    "trend_add_block_bp", "trend_add_block_q",
    "entry_block_symbols",
    "trend_only_q", "max_one_side_seconds", "min_hold_seconds",
    "sudden_move_bp", "sudden_move_k", "sudden_move_cooldown_sec",
    "vol_pause_sigma", "toxic_streak", "toxic_bp",
    "markout_halt_bp", "markout_window_n", "markout_horizon_sec",
    "markout_halts_adds", "markout_halts_sides",
    "daily_loss_stop_pct", "stop_loss_bp", "take_profit_bp",
    "max_leg_notional_mult", "stop_maker_grace_sec",
)
PARAM_KEYS = (
    "side_mode", "w_base_bp", "min_width_bp", "k_vol", "k_inv", "k_trend",
    "frozen_width_bp", "frozen_max_move_bp", "frozen_lookback",
    "spread_mult", "spread_mult_reduce", "inv_skew_abs_bp",
)
GATE_KEYS = ("skip_counts", "gate_probe_counts", "lane_pause_counts",
             "lane_pause_last")


def _show(title: str) -> None:
    print("\n" + "-" * 88)
    print(title)
    print("-" * 88)


def main() -> int:
    only_gates = "--gates" in sys.argv
    if not STATUS.exists():
        print(f"✗ 状态文件不存在：{STATUS}")
        return 1
    raw = STATUS.read_text(encoding="utf-8", errors="replace").strip()
    try:
        st = json.loads(raw)
    except Exception as e:
        print(f"✗ 状态文件不是合法 JSON：{e}")
        print(f"  前 300 字符：{raw[:300]}")
        return 1

    import time
    age = time.time() - float(st.get("ts") or 0)
    print("=" * 88)
    print("h631 — mm_lane_status.json 读数")
    print("=" * 88)
    print(f"  文件      = {STATUS}")
    print(f"  心跳年龄  = {age:.1f}s  {'✓ 新鲜' if age < 60 else '✗ 陈旧（worker 可能没在 tick）'}")
    print(f"  ok        = {st.get('ok')}  reason={st.get('reason')!r}")

    if not only_gates:
        _show("1) 活性")
        for k in ("ticks", "fills", "flattens", "last_tick_ts", "equity",
                  "fill_notional", "fills_per_hour"):
            print(f"  {k:22s} = {st.get(k)!r}")
        print(f"  {'symbols':22s} = {st.get('symbols')!r}")

        _show("2) 报价行为（决定是否有腿）")
        for k in ("quoted_decisions", "side_counts", "sigma_decisions",
                  "quote_modes", "frozen_share", "avg_width_bp", "avg_base_bp"):
            print(f"  {k:22s} = {json.dumps(st.get(k), ensure_ascii=False)}")

    _show("3) 门禁（谁在拦）")
    sk = st.get("skip_counts") or {}
    if isinstance(sk, dict) and sk:
        tot = sum(v for v in sk.values() if isinstance(v, (int, float)))
        for k, v in sorted(sk.items(), key=lambda kv: -(kv[1] or 0)):
            pct = (100.0 * v / tot) if tot else 0.0
            print(f"  {k:26s} = {v:>8}   ({pct:5.1f}% of counted)")
        print(f"  {'':26s}   {'-' * 8}")
        print(f"  {'合计':26s} = {tot:>8}")
    else:
        print("  skip_counts 空 ✓（没有任何门禁在拦截）")
    for k in GATE_KEYS:
        if k in ("skip_counts",):
            continue
        if k in st:
            print(f"  {k:26s} = {json.dumps(st.get(k), ensure_ascii=False)}")
        else:
            print(f"  {k:26s} = ✗ 键不在心跳里"
                  "（mm_lane_worker._snapshot 的显式白名单没列出它 ⇒ 读数不可见）")

    if not only_gates:
        _show("4) 生效参数/限额（相关键）")
        lim = st.get("limits") or {}
        par = st.get("params") or {}
        for k in PARAM_KEYS:
            if k in par:
                print(f"  params.{k:24s} = {par[k]!r}")
        for k in LIMIT_KEYS:
            if k in lim:
                print(f"  limits.{k:24s} = {lim[k]!r}")
        miss = [k for k in LIMIT_KEYS if k not in lim]
        if miss:
            print(f"  （limits 里没有：{', '.join(miss)}）")

        _show("5) 每币运行态")
        stt = st.get("states") or {}
        if not stt:
            print("  空")
        for sym, s in stt.items():
            print(f"  {sym:8s} qty={s.get('qty')!r} avg_px={s.get('avg_px')!r} "
                  f"bid={s.get('quote_bid')!r} ask={s.get('quote_ask')!r} "
                  f"quote_ts={s.get('quote_ts')!r}")

    print("\n" + "=" * 88)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
