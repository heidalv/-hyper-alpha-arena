# -*- coding: utf-8 -*-
"""[2026-09-14 F121] 参数**接线**审计（行为式）：每个旋钮真的会改变决策吗？

为什么需要：静态 grep 会误判——字段可能通过 `p.xxx`、`getattr(params, "xxx")`、
位置参数等多种方式被读取；反过来，**定义了但从未被任何判定使用**的字段（实测
`max_symbol_notional_ratio` 就是）会被误当成"已接线" ✗。"参数无逻辑错误"要求能
**证明**每个配置项都有效力。

做法（行为式）：对每个字段，把它推到极端值（0 / 极大），在**多个场景**下调用
`plan_tick`，与"该字段取默认值"的同一场景比较决策输出
（挂单价、成交量、skip 原因、车道暂停）。任一场景输出不同 ⇒ 该旋钮**已接线** ✓；
所有场景都完全相同 ⇒ **死旋钮** ✗（要么接线，要么从配置里删掉）。

场景覆盖：正常报价 / 区间穿越成交 / 多头库存偏斜 / 浮亏 / 高波动 / 毒性流 /
挂单陈旧 / 高 OFI / 趋势 / 日亏 / 敞口吃紧 / 单边超时。
"""
from __future__ import annotations

import sys
from dataclasses import fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook, LaneRiskLimits, QuoteParams,
)


def _state(**kw):
    from backend.services.market_maker.runner import SymbolState
    st = SymbolState(symbol="BTC")
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _scenarios():
    """每个场景 = plan_tick 的完整入参（除 params/limits）。"""
    import time
    now = time.time()
    base_hist = [100.0 + (i % 3) * 0.01 for i in range(60)]
    out = []

    def add(name, **over):
        st = _state(mid_hist=list(base_hist), vol_baseline_bp=1.5)
        book = InventoryBook()
        arg = dict(state=st, mid=100.0, seg_low=0.0, seg_high=0.0,
                   seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now,
                   equity=300.0, fill_notional=300.0, taker_fee_bp=4.0,
                   maker_fee_bp=0.0, half_spread=0.01, sigma_norm=0.0,
                   book=book, marks={"BTC": 100.0}, ofi=0.0, day_pnl_usd=0.0,
                   pending={"up": 0.0, "down": 0.0, "gross": 0.0},
                   lane_limits_enforce=True)
        arg.update(over)
        out.append((name, arg))
        return arg

    add("正常报价")
    add("区间穿越(买侧)", seg_low=99.0, seg_high=101.0, seg_taker_sell=5.0,
        seg_taker_buy=5.0, state=_state(mid_hist=list(base_hist), vol_baseline_bp=1.5,
                                        quote_bid=99.5, quote_ask=100.5, quote_ts=now - 5))
    a = add("多头库存", state=_state(mid_hist=list(base_hist), vol_baseline_bp=1.5,
                                 qty=2.0, avg_px=100.0, avg_mid=100.0,
                                 opened_ts=now - 30, last_ts=now))
    a["book"].positions["BTC"] = type(a["book"].positions.get("BTC") or object())()

    from backend.services.market_maker.core import Position
    a["book"].positions["BTC"] = Position(qty=2.0, avg_px=100.0, avg_mid=100.0,
                                         opened_ts=now - 30, last_ts=now)
    b = add("浮亏持仓", state=_state(mid_hist=list(base_hist), vol_baseline_bp=1.5,
                                  qty=2.0, avg_px=110.0, avg_mid=110.0,
                                  opened_ts=now - 60, last_ts=now))
    b["book"].positions["BTC"] = Position(qty=2.0, avg_px=110.0, avg_mid=110.0,
                                         opened_ts=now - 60, last_ts=now)
    add("高波动", sigma_norm=3.0)
    # [F193 2026-09-15] 补"中高波动（未触发 σ 闸）"场景。
    # 为什么必须补：F189 把**运行态** `vol_pause_sigma` 从 0（关闭）改成 1.0（armed）✓，
    # 于是上面那个 `sigma_norm=3.0` 的场景在所有探针下都直接"暂停该币"✗ ⇒ 决策对
    # 任何下游旋钮都一样 ⇒ `k_vol` 被**误判成死旋钮** ✗✗。
    # 教训（与 F121 同源）：接线探针必须保证"被测旋钮在**至少一个场景**里可观测"，
    # 否则闸门一 armed，被它挡在后面的旋钮就会集体"假死" ✓。
    # σ=0.5：高于 0 足以让 `k_vol` 改变挂宽 ✓，又低于任何合理的暂停阈值（现网 1.0）✓。
    add("中高波动(未触发σ闸)", sigma_norm=0.5)
    c = add("毒性连击", state=_state(mid_hist=list(base_hist), vol_baseline_bp=1.5,
                                 toxic_streak=9))
    add("挂单陈旧", state=_state(mid_hist=list(base_hist), vol_baseline_bp=1.5,
                              quote_bid=99.0, quote_ask=101.0, quote_ts=now - 600))
    add("高OFI", ofi=0.95)
    d = add("趋势行情", state=_state(mid_hist=[100.0 + i * 0.05 for i in range(60)],
                                  vol_baseline_bp=1.5))
    _ = d
    add("日亏", day_pnl_usd=-60.0)
    e = add("敞口吃紧", pending={"up": 280.0, "down": 280.0, "gross": 560.0})
    e["book"].positions["ETH"] = Position(qty=2.8, avg_px=100.0, avg_mid=100.0,
                                         opened_ts=now - 10, last_ts=now)
    e["marks"]["ETH"] = 100.0
    f = add("单边超时", state=_state(mid_hist=list(base_hist), vol_baseline_bp=1.5,
                                 qty=2.0, avg_px=100.0, avg_mid=100.0,
                                 opened_ts=now - 5000, last_ts=now))
    f["book"].positions["BTC"] = Position(qty=2.0, avg_px=100.0, avg_mid=100.0,
                                         opened_ts=now - 5000, last_ts=now)
    return out


def _run(params, limits, arg):
    from backend.services.market_maker.runner import plan_tick
    dec, _meta = plan_tick(params=params, limits=limits, **arg)
    return (round(float(dec.bid or 0.0), 8), round(float(dec.ask or 0.0), 8),
            tuple(sorted((f.side, round(float(f.qty or 0.0), 8)) for f in dec.fills)),
            str(dec.skip or ""), str(getattr(dec, "lane_pause", "") or ""))


def _wired(field: str, is_limit: bool, p_base, l_base) -> bool:
    """把该字段推到 0 与极大值，任一场景决策改变 ⇒ 已接线。

    三个必须守住的细节（前两版都踩了）：
      1. 基准必须是**运行态实际使用的** params/limits（`r.params` / `r.limits`）——
         注册表把风控键放在 `meta.params` 里再由 `get_runner` 分流，若自己去
         `meta.limits` 取会拿到 **dataclass 默认值**（例如方向上限 0.1 = $30），
         结果所有场景都卡在同一道闸门 ⇒ 下游所有旋钮都被误判成"死旋钮" ✗；
      2. 每个场景必须在**每次运行前重建**——`plan_tick` 会改库存/挂单，
         复用同一批场景对象会造成状态泄漏、输出不可复现 ✗；
      3. 只改一个字段，其余保持运行态取值 ⇒ 差异只能归因于该字段 ✓。
    """
    ref = [_run(p_base, l_base, a) for _, a in _scenarios()]
    # 三个极端：0 / 极小正值 / 极大值。
    # 为什么必须有**极小正值**：按"0 = 显式关闭"的约定，很多闸门的运行态取值就是 0
    # （实测 vol_pause_sigma / stop_loss_bp / trend_pause_bp / ofi_flatten_* 都是 0）；
    # 此时 0 与 1e9 都等价于"关闭" ⇒ 两个极端输出相同 ⇒ 会把**已经接线**的闸门
    # 误判成死旋钮 ✗。极小正值能让"关闭 ⇒ 开启"的变化显形 ✓。
    for extreme in (0.0, 1e-6, 1e9):
        if is_limit:
            p, l = p_base, replace(l_base, **{field: extreme})
        else:
            p, l = replace(p_base, **{field: extreme}), l_base
        for (name, a), r0 in zip(_scenarios(), ref):
            try:
                r1 = _run(p, l, a)
            except Exception:
                return True          # 抛错也算"这个旋钮有影响力"（行为变了）
            if r1 != r0:
                return True
    return False


def test_every_configured_knob_changes_a_decision():
    """行为式接线审计：核心旋钮必须能改变决策；其余旋钮**报告**覆盖情况。

    断言范围刻意保守：只断言本文件场景**确实覆盖到**的核心旋钮（否则会因为场景
    未构造出触发条件而误报死旋钮 ✗，例如 `frozen_*` 需要
    `len(mid_hist) >= frozen_lookback+1 = 241` 期才会计算"单步最大移动"）。
    其余旋钮打印出来供人工核对，未覆盖 ≠ 未接线。
    """
    import json

    from backend.services import lane_registry as reg
    from backend.services.market_maker.runner import get_runner

    lane = reg.get_lane("mm_asterdex") or {}
    meta = lane.get("meta") or {}
    cfg = {k: v for k, v in dict(meta.get("params") or {}).items()}
    cfg.update(dict(meta.get("limits") or {}))
    r = get_runner("mm_asterdex")
    pfields = {f.name for f in fields(QuoteParams)}
    lfields = {f.name for f in fields(LaneRiskLimits)}
    p_base, l_base = r.params, r.limits
    aliases = {"fill_notional", "compound_ratio"}
    # 本文件场景已覆盖的核心旋钮（报价宽度/库存偏斜/宽度上下限/敞口上限/σ 闸/OFI 闸）
    core = {"w_base_bp", "k_vol", "k_inv", "min_width_bp", "min_width_reduce_bp",
            "max_width_bp", "max_net_exposure_ratio", "max_net_directional_ratio",
            "vol_pause_sigma", "ofi_block_threshold", "toxic_streak", "daily_loss_stop_pct",
            "max_one_side_seconds", "max_quote_age_sec"}

    dead, uncovered = [], []
    for k in sorted(cfg):
        if k in aliases or (k not in pfields and k not in lfields):
            continue
        if _wired(k, k in lfields, p_base, l_base):
            continue
        (dead if k in core else uncovered).append(k)
    print("核心旋钮中断线的:", json.dumps(dead, ensure_ascii=False))
    print("未被本文件场景覆盖（需补场景/人工核对）:", json.dumps(uncovered, ensure_ascii=False))
    assert not dead, f"核心旋钮未接线（必须修）: {dead}"


def test_known_dead_knob_stays_documented():
    """已确认的死旋钮清单（回归保护）：一旦接线，本测试会失败并提醒更新文档。

    现场证据（两条独立路径）：
      · 全仓 grep：`max_symbol_notional_ratio` 除 dataclass 定义外无任何读取点；
      · 行为扫描（F120）：1.0/1.5/2.0/3.0 四档在 24h 回放上输出**完全相同**。
    真正生效的单币敞口上限是 `max_net_directional_ratio`（1.0 = 每币 1 腿）。
    """
    from backend.services.market_maker.core import LaneRiskLimits, QuoteParams

    KNOWN_DEAD = {"max_symbol_notional_ratio"}
    r = None
    try:
        from backend.services.market_maker.runner import get_runner
        r = get_runner("mm_asterdex")
    except Exception:
        pass
    p_base = (r.params if r else QuoteParams())
    l_base = (r.limits if r else LaneRiskLimits())
    still_dead = {k for k in KNOWN_DEAD if not _wired(k, True, p_base, l_base)}
    assert still_dead == KNOWN_DEAD, (
        f"死旋钮清单已变化（{KNOWN_DEAD - still_dead} 现在已被读取）⇒ "
        f"请更新 core.py 注释与本清单")
