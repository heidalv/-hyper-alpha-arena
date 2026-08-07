"""Paper Engine One-Way 净额计算测试。

验证净额风险 + 分层记账的核心不变量:
- 同币种 long+short 对冲对释放保证金 (net_margin < row_margin_sum)
- 净头寸翻转 (long → short via 反向超量订单)
- 杠杆跨方向统一 (One-Way: 同币种一个杠杆)
- 净额爆仓价基于净方向
- 开关 PAPER_NETTING_MODE=false 回退旧行为
"""
from types import SimpleNamespace

from backend.services.paper_netting import (
    NetPosition,
    aggregate_rows_to_net,
    calc_net_liquidation_price,
    compute_margin_delta_for_order,
    net_side_from_signed,
    signed_size,
)


def _pos(symbol, side, size, entry_price, leverage, margin, unrealized_pnl=0.0):
    """构造一个轻量 PaperPosition mock（仅净额计算需要的字段）。"""
    return SimpleNamespace(
        symbol=symbol,
        side=side,
        size=float(size),
        entry_price=float(entry_price),
        leverage=float(leverage),
        margin=float(margin),
        unrealized_pnl=float(unrealized_pnl),
    )


# ────────────────────────────────────────────────────────────────────
# 基础工具
# ────────────────────────────────────────────────────────────────────

def test_signed_size_long_positive():
    assert signed_size("long", 0.1) == 0.1
    assert signed_size("buy", 0.1) == 0.1


def test_signed_size_short_negative():
    assert signed_size("short", 0.1) == -0.1
    assert signed_size("sell", 0.1) == -0.1


def test_signed_size_handles_invalid():
    assert signed_size("", 0.1) == 0.0
    assert signed_size("unknown", 0.1) == 0.0
    assert signed_size("long", None) == 0.0


def test_net_side_from_signed():
    assert net_side_from_signed(0.5) == "long"
    assert net_side_from_signed(-0.3) == "short"
    assert net_side_from_signed(0.0) == "flat"
    assert net_side_from_signed(1e-13) == "flat"  # 近零容差


def test_net_liquidation_price_long_below_entry():
    # long 5x @ 60000 → liq ≈ 60000 * (1 - 0.2 + 0.005) = 48300
    liq = calc_net_liquidation_price(60000.0, "long", 5.0, 0.005)
    assert 48000 < liq < 48500


def test_net_liquidation_price_short_above_entry():
    # short 5x @ 60000 → liq ≈ 60000 * (1 + 0.2 - 0.005) = 71700
    liq = calc_net_liquidation_price(60000.0, "short", 5.0, 0.005)
    assert 71500 < liq < 72000


def test_net_liquidation_price_flat_zero():
    assert calc_net_liquidation_price(60000.0, "flat", 5.0, 0.005) == 0.0


def test_net_liquidation_price_leverage_one_zero():
    # leverage <= 1 → 0 (无爆仓风险)
    assert calc_net_liquidation_price(60000.0, "long", 1.0, 0.005) == 0.0


# ────────────────────────────────────────────────────────────────────
# 聚合: 单方向
# ────────────────────────────────────────────────────────────────────

def test_aggregate_single_long():
    rows = [_pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 100.0)]
    np_ = aggregate_rows_to_net("BTC", rows)
    assert np_.net_side == "long"
    assert abs(np_.net_size - 0.1) < 1e-9
    assert abs(np_.net_signed_size - 0.1) < 1e-9
    assert abs(np_.net_weighted_entry - 60000.0) < 1e-6
    assert np_.unified_leverage == 5.0
    assert np_.row_count == 1
    # net_margin = 0.1 * 60000 / 5 = 1200
    assert abs(np_.net_margin - 1200.0) < 1e-6
    # 单仓无对冲释放
    assert np_.hedge_release < 1e-6


def test_aggregate_same_side_dca_merges():
    # 两个 long 子仓 (不同 trade_nature 但同方向) → 净额合并
    rows = [
        _pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 50.0),
        _pos("BTC", "long", 0.2, 63000.0, 5.0, 2520.0, 80.0),
    ]
    np_ = aggregate_rows_to_net("BTC", rows)
    assert np_.net_side == "long"
    assert abs(np_.net_size - 0.3) < 1e-9
    # 加权均价: (60000*0.1 + 63000*0.2) / 0.3 = 62000
    assert abs(np_.net_weighted_entry - 62000.0) < 1e-6
    # uPnL 代数和
    assert abs(np_.net_unrealized_pnl - 130.0) < 1e-6


# ────────────────────────────────────────────────────────────────────
# 聚合: 对冲对 (核心场景 — 净额保证金释放)
# ────────────────────────────────────────────────────────────────────

def test_net_long_short_offset_releases_margin():
    # scalp short 0.05 + trend long 0.1 → 净 long 0.05
    rows = [
        _pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 100.0),
        _pos("BTC", "short", 0.05, 61000.0, 5.0, 610.0, -20.0),
    ]
    np_ = aggregate_rows_to_net("BTC", rows)
    assert np_.net_side == "long"
    assert abs(np_.net_size - 0.05) < 1e-9
    # 净均价: (60000*0.1 - 61000*0.05) / 0.05 = 59000
    assert abs(np_.net_weighted_entry - 59000.0) < 1e-6
    # 行级 margin 求和 = 1810
    assert abs(np_.row_margin_sum - 1810.0) < 1e-6
    # 净保证金 = 0.05 * 59000 / 5 = 590
    assert abs(np_.net_margin - 590.0) < 1e-6
    # 释放 1810 - 590 = 1220
    assert abs(np_.hedge_release - 1220.0) < 1e-6


def test_net_full_hedge_margin_zero():
    # 完全对冲: long 0.1 + short 0.1 → 净额 0，保证金全部释放
    rows = [
        _pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 50.0),
        _pos("BTC", "short", 0.1, 61000.0, 5.0, 1220.0, -30.0),
    ]
    np_ = aggregate_rows_to_net("BTC", rows)
    assert np_.net_side == "flat"
    assert np_.net_size < 1e-9
    assert np_.net_margin < 1e-6  # 净额 0 → 保证金 0
    # 全部行级保证金释放
    assert abs(np_.hedge_release - 2420.0) < 1e-6


def test_net_flip_long_to_short():
    # short 量超过 long → 净方向翻转
    # long 0.1 + short 0.3 → 净 short 0.2
    rows = [
        _pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 100.0),
        _pos("BTC", "short", 0.3, 62000.0, 5.0, 3720.0, -150.0),
    ]
    np_ = aggregate_rows_to_net("BTC", rows)
    assert np_.net_side == "short"
    assert abs(np_.net_size - 0.2) < 1e-9
    assert abs(np_.net_signed_size - (-0.2)) < 1e-9
    # 净均价: (60000*0.1 - 62000*0.3) / (-0.2) = (6000-18600)/(-0.2) = 63000
    assert abs(np_.net_weighted_entry - 63000.0) < 1e-6


# ────────────────────────────────────────────────────────────────────
# 杠杆统一 (跨方向取最大)
# ────────────────────────────────────────────────────────────────────

def test_unified_leverage_cross_side_max():
    # long 3x + short 5x → 统一 5x (HL 同币种一个杠杆)
    rows = [
        _pos("BTC", "long", 0.1, 60000.0, 3.0, 2000.0, 100.0),
        _pos("BTC", "short", 0.05, 61000.0, 5.0, 610.0, -20.0),
    ]
    np_ = aggregate_rows_to_net("BTC", rows)
    assert np_.unified_leverage == 5.0


def test_unified_leverage_single_row_keeps():
    rows = [_pos("BTC", "long", 0.1, 60000.0, 7.0, 857.0, 0.0)]
    np_ = aggregate_rows_to_net("BTC", rows)
    assert np_.unified_leverage == 7.0


# ────────────────────────────────────────────────────────────────────
# 保证金增量计算 (开仓前审计)
# ────────────────────────────────────────────────────────────────────

def test_margin_delta_open_new_zero_existing():
    # 无现有仓位 → 全新开仓，delta = 完整保证金
    cur = NetPosition(symbol="BTC")  # 净额 0
    delta, scenario = compute_margin_delta_for_order(
        cur, "buy", 0.1, 60000.0, 5.0,
    )
    assert scenario == "open_new"
    # 0.1 * 60000 / 5 = 1200
    assert abs(delta - 1200.0) < 1e-6


def test_margin_delta_add_same_side():
    # 现有 long 0.1 → 再加 long 0.05，同向加仓
    cur = aggregate_rows_to_net("BTC", [_pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 0.0)])
    delta, scenario = compute_margin_delta_for_order(
        cur, "buy", 0.05, 60000.0, 5.0,
    )
    assert scenario == "add_same_side"
    # 新净额 0.15, 旧 0.1 → delta = (0.15-0.1)*60000/5 = 600
    assert abs(delta - 600.0) < 1e-6


def test_margin_delta_partial_hedge_releases():
    # 现有 long 0.1 → 反向 sell 0.03 (部分对冲，仍 net long)
    cur = aggregate_rows_to_net("BTC", [_pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 0.0)])
    delta, scenario = compute_margin_delta_for_order(
        cur, "sell", 0.03, 60000.0, 5.0,
    )
    # 部分对冲: 新净额 0.07 long, 旧 0.1 → delta = max(0, 0.07*60000/5 - 1200) = max(0, 840-1200) = 0
    assert scenario == "partial_hedge"
    assert delta < 1e-6  # 释放，无新增


def test_margin_delta_full_hedge_flip():
    # 现有 long 0.1 → 反向 sell 0.3 (翻转)
    cur = aggregate_rows_to_net("BTC", [_pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 0.0)])
    delta, scenario = compute_margin_delta_for_order(
        cur, "sell", 0.3, 60000.0, 5.0,
    )
    # 翻转场景: 释放旧保证金，但新净额 0.2 short 需要保证金
    # compute_margin_delta_for_order 用 order_price 估算: 0.2 * 60000 / 5 = 2400
    # 旧 1200 → delta = max(0, 2400-1200) = 1200
    assert scenario == "full_hedge_flip"
    assert delta > 0


def test_margin_delta_full_hedge_no_new_position():
    # 现有 long 0.1 → 反向 sell 0.1 (完全平掉，无新仓位)
    cur = aggregate_rows_to_net("BTC", [_pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 0.0)])
    delta, scenario = compute_margin_delta_for_order(
        cur, "sell", 0.1, 60000.0, 5.0,
    )
    # 完全平掉 → delta = 0, 释放全部
    assert scenario == "full_hedge_flip"
    assert delta < 1e-9


# ────────────────────────────────────────────────────────────────────
# 空输入
# ────────────────────────────────────────────────────────────────────

def test_aggregate_empty_rows():
    np_ = aggregate_rows_to_net("BTC", [])
    assert np_.net_side == "flat"
    assert np_.net_size == 0.0
    assert np_.net_margin == 0.0
    assert np_.row_count == 0


def test_aggregate_handles_negative_size_defensive():
    # 防御: size 为负的 long → signed_size 应为正 (abs)
    rows = [_pos("BTC", "long", -0.1, 60000.0, 5.0, 1200.0, 0.0)]
    np_ = aggregate_rows_to_net("BTC", rows)
    # long + 负 size → signed_size = +0.1 (abs)
    assert abs(np_.net_signed_size - 0.1) < 1e-9


# ────────────────────────────────────────────────────────────────────
# 序列化
# ────────────────────────────────────────────────────────────────────

def test_net_position_to_dict_fields():
    rows = [_pos("BTC", "long", 0.1, 60000.0, 5.0, 1200.0, 100.0)]
    np_ = aggregate_rows_to_net("BTC", rows)
    d = np_.to_dict()
    assert d["symbol"] == "BTC"
    assert d["net_side"] == "long"
    assert "net_margin" in d
    assert "hedge_release" in d
    assert "net_liquidation_price" in d


# ────────────────────────────────────────────────────────────────────
# 开关
# ────────────────────────────────────────────────────────────────────

def test_is_netting_enabled_default_true():
    from backend.services.paper_netting import is_netting_enabled
    # 默认 settings.PAPER_NETTING_MODE = True
    assert is_netting_enabled() is True
