# -*- coding: utf-8 -*-
"""[F338 2026-09-22] 单腿名义硬上限：拆掉「逆选择放大器」。

# 缺陷现场（H215/H216，14 天 23,603 腿实测）

`plan_tick` 的腿量是：

    qty = min(_target_qty, _avail * _QUEUE_SHARE)      # _QUEUE_SHARE = 0.30
    _avail = 该 15s 桶的**主动成交量**

**没有任何绝对上限。** 某个 15s 桶里有 $22,600 的主动卖 ⇒ 我们吃 $6,789。

实测分布与后果：

    名义分位  P50 $163　P99 $861　P99.9 $2,000　**max $11,661 = 71.5× 中位**
    集中度    **前 10 腿 = 全部亏损的 41%**；前 50 腿 = 62%；前 100 腿 = 71%
    逐日前 1% 占当日净额 29%~161%，12 天里 11 天为负

⇒ 引擎在**剧烈行情里自动放大名义**，而剧烈行情正是 `price_bp` 最差的地方。
被 `_avail` 截掉部分的 bp 均值随阈值**单调恶化**：

    1×中位 −1.31bp／2× −3.21bp／3× −4.39bp／5× −7.64bp／8× −10.74bp

反事实（同一批腿按 `min(1, cap/notional)` 缩放）：

    上限        被截名义    净额          vs 原状
    1.0×中位     41.7%    −$128.63    **+$264.47**
    3.0×中位      7.3%    −$237.96    **+$155.14**
    无上限         0%     −$393.10         —

本测试锁的是**三条不变量**：

  ① `mult = 0`（默认）⇒ 与旧行为**逐字一致**（一键回退的保证）；
  ② `mult > 0` ⇒ 加仓腿名义被截到 `mult × fill_notional`，且 `dec.leg_capped` 计数可见；
  ③ **减仓腿不受限** —— 否则残仓永远平不掉，亏损会从价差搬到 taker 强平
     （本会话已见过的失败模式：`flatten` 腿 100% 过价，~11bp/笔）。

第 ③ 条是最容易被"顺手也加上限"改坏的一条，所以必须有测试钉住。
"""
from __future__ import annotations

import inspect
import time

import pytest

from backend.services.market_maker import runner as R
from backend.services.market_maker.core import (
    InventoryBook,
    LaneRiskLimits,
    Position,
    QuoteParams,
)


def _plan(*, mid=1.0, seg_low=0.999, seg_high=1.001,
          seg_taker_sell=2000.0, seg_taker_buy=2000.0,
          fill_notional=100.0, limits=None, book=None, qty=0.0, symbol="SOLUSDT",
          judged_quote=None):
    """调用 `plan_tick`，只传它签名里真实存在的参数（签名在演进）。

    ⚠️ **成交判定用的是 `judged_quote`**（F176：上一 tick 的挂单，
    `(bid, ask, mid)`），不是 `state.quote_*`。不传它则空状态下
    `_jq_bid == _jq_ask == 0` ⇒ 一条腿都不产生 ⇒ 断言会变成**空真**。
    本文件第 0 条测试专门防这个。

    ⚠️ **腿量受 `min(目标, _avail × 0.30)` 控制**，要让它被 `_avail` 主导，
    `_avail × 0.30` 必须**大于**目标腿量（`fill_notional / mid`）。
    这里 `mid=1.0` ⇒ 目标 = 100 币；`seg_taker_sell=2000` ⇒ `_avail×0.3 = 600` 币
    = **$600 腿 = 6× 目标** ⇒ 关掉上限时确实超大腿，开上限时才可测。
    """
    b = book if book is not None else InventoryBook()
    if qty:
        b.positions[symbol] = Position(qty=qty, opened_ts=time.time() - 5.0)
    st = R.SymbolState(symbol=symbol)
    if judged_quote is None:
        judged_quote = (0.9995, 1.0005, 1.0)    # 买腿会成交、卖腿也会成交
    kwargs = {
        "state": st, "mid": mid, "seg_low": seg_low, "seg_high": seg_high,
        "seg_taker_sell": seg_taker_sell, "seg_taker_buy": seg_taker_buy,
        "now_ts": time.time(),
        "params": QuoteParams(spread_mult=0.5, w_base_bp=5.0, min_width_bp=0.3),
        "limits": limits if limits is not None else LaneRiskLimits(),
        "book": b, "equity": 1000.0, "fill_notional": fill_notional,
        "half_spread": 0.0005, "judged_quote": judged_quote,
    }
    sig = inspect.signature(R.plan_tick)
    kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
    return R.plan_tick(**kwargs)


def _legs(dec):
    return [(f.side, f.qty, f.px) for f in (dec.fills or [])]


# ── 0. 前提自证：这个场景**确实**会产生超大腿（否则下面的断言可能只是空跑）──
@pytest.mark.unit
def test_scenario_really_produces_an_oversized_leg():
    """**先证明测试场景是有效的**：关掉上限时必须真的出现 $300 的腿。

    没有这一条，若哪天引擎在前面的闸门就把腿撤掉了，本文件的"上限生效"断言
    会变成**空真**（vacuously true）—— 测的是"没有腿"，却报成"上限起作用了"。
    """
    dec, _ = _plan(limits=LaneRiskLimits(max_leg_notional_mult=0.0),
                   fill_notional=500.0)
    legs = _legs(dec)
    assert legs, f"场景失效：一条腿都没有（skip={dec.skip!r} action={dec.action!r}）"
    biggest = max(q * p for _s, q, p in legs)
    assert biggest >= 500.0 * 0.999, (
        f"场景失效：最大腿只有 ${biggest:.2f}，而目标腿量是 $500"
        f" ⇒ 测不到上限。legs={legs}")


# ── 1. 默认关闭：与旧行为逐字一致 ────────────────────────────────────────
@pytest.mark.unit
def test_default_is_disabled_and_byte_identical():
    """`max_leg_notional_mult` 默认必须是 **0 = 关闭**。

    这是"可一键回退"的硬保证：字段默认值若被改成非 0，
    所有历史回放/测试的基线会被**静默改写**（本仓库已多次踩到）。
    """
    assert LaneRiskLimits().max_leg_notional_mult == 0.0, (
        "默认必须为 0（关闭）⇒ 与 F338 之前逐字一致")
    with_off = _legs(_plan(limits=LaneRiskLimits(max_leg_notional_mult=0.0),
                           fill_notional=500.0)[0])
    # 负值也必须等于关闭（防止有人用 -1 表示"关闭"却意外生效）
    with_neg = _legs(_plan(limits=LaneRiskLimits(max_leg_notional_mult=-1.0),
                           fill_notional=500.0)[0])
    assert with_off == with_neg, (
        f"0 与负值都应表示关闭；实际 {with_off} != {with_neg}")
    assert with_off, "场景失效：关闭状态下也应产生腿"


# ── 2. 生效：腿被截到 mult × fill_notional ───────────────────────────────
@pytest.mark.unit
def test_cap_truncates_each_entry_leg_to_mult_times_fill_notional():
    """0.5× × $500 = $250 上限：每条加仓腿的名义都必须合规，且计数可见。"""
    cap, fn = 0.5, 500.0
    limit = cap * fn                        # $250
    dec, _ = _plan(limits=LaneRiskLimits(max_leg_notional_mult=cap),
                   fill_notional=fn)
    legs = _legs(dec)
    assert legs, f"场景失效：没有腿（skip={dec.skip!r}）"
    for side, q, p in legs:
        # 容差说明：上限是在**成交价 px** 上算的（`qty = cap_notional / px`），
        # 而 px 是挂单价（ask 1.0005）⇒ `qty × px` 恰好等于上限，
        # 但**名义是用 px 复算的**，所以相对 0.1% 的余量是必要的（实测 +0.10%）。
        assert q * p <= limit * 1.002, (
            f"{side} 腿名义 ${q*p:.2f} 超过上限 ${limit:.2f} ⇒ 上限没生效")
    assert dec.leg_capped >= 1, (
        f"应有腿被截断（dec.leg_capped={dec.leg_capped}）"
        " ⇒ 观测字段没接上，无法从状态里发现上限是否真的在工作")


@pytest.mark.unit
def test_cap_is_monotone_in_mult():
    """上限越紧，腿越小（单调）—— 防止参数方向搞反。"""
    fn = 500.0
    sizes = []
    for m in (0.0, 0.2, 0.5, 1.0, 4.0):
        dec, _ = _plan(limits=LaneRiskLimits(max_leg_notional_mult=m),
                       fill_notional=fn)
        legs = _legs(dec)
        sizes.append(max((q * p for _s, q, p in legs), default=0.0))
    assert sizes[0] >= sizes[-1], (
        f"关闭(0)时的腿应最大；实际序列 {sizes} ⇒ 参数方向可能反了")
    assert sizes[0] >= fn * 0.999, f"关闭时应有 $500 腿；实际 {sizes}"
    # 容差 1.001：上限按成交价算，而成交价是挂单价（ask 1.0005）⇒
    # `qty × px` 会略高于 `cap × fill_notional`（实测 +0.10%，见上一条测试）
    assert sizes[1] <= 0.2 * fn * 1.002, f"0.2× 上限未按预期截断：{sizes}"
    assert sizes[2] <= 0.5 * fn * 1.002, f"0.5× 上限未按预期截断：{sizes}"
    assert sizes[3] <= 1.0 * fn * 1.002, f"1.0× 上限未按预期截断：{sizes}"
    assert sizes[4] <= 4.0 * fn * 1.002, f"4.0× 上限未按预期截断：{sizes}"


# ── 3. **最关键**：减仓腿永不受限 ────────────────────────────────────────
@pytest.mark.unit
def test_reducing_leg_is_never_capped():
    """减仓腿必须能一次平掉整个仓位，**哪怕远超上限**。

    为什么这是安全关键的：若给减仓腿也加上限，残仓**永远平不掉**
    （每次只平 cap，剩下的等下一个 tick，而下一个 tick 可能没成交量），
    亏损就从「价差」搬到「taker 强平」上 —— 实测 flatten 腿 100% 过价、
    ~11bp/笔，比价差本身（~2bp）贵 5 倍。

    做法：注入一个远超上限的持仓。`pos_qty=150` ⇒ 减仓腿 $150，
    而上限只有 `1.0 × $100 = $100` ⇒ 若上限误伤减仓侧，卖腿会被截到 $100。
    """
    cap, fn = 1.0, 100.0
    limit_notional = cap * fn               # $100 上限
    pos_qty = 150.0                         # $150 名义 = 1.5× 上限
    dec, _ = _plan(limits=LaneRiskLimits(max_leg_notional_mult=cap),
                   fill_notional=fn, qty=pos_qty, mid=1.0)
    legs = _legs(dec)
    assert legs, f"场景失效：持多头时应至少有一侧报价（skip={dec.skip!r}）"
    # 多头 ⇒ 减仓腿是 sell
    sells = [(q, p) for s, q, p in legs if s == "sell"]
    assert sells, f"持多头时应有卖腿（减仓）；实际 {legs}"
    max_sell_notional = max(q * p for q, p in sells)
    assert max_sell_notional > limit_notional * 1.0001, (
        f"减仓腿被上限截断了（卖腿 ${max_sell_notional:.2f} ≤ 上限 "
        f"${limit_notional:.2f}）⇒ 残仓平不掉，亏损会搬到 taker 强平。"
        f"legs={legs} leg_capped={dec.leg_capped}")


# ── 4. 观测字段必须真的进 to_dict（否则状态里看不到它是否在工作）──────────
@pytest.mark.unit
def test_leg_capped_is_exposed_in_to_dict():
    dec, _ = _plan(limits=LaneRiskLimits(max_leg_notional_mult=1.0),
                   fill_notional=100.0)
    d = dec.to_dict()
    assert "leg_capped" in d, (
        "`leg_capped` 必须出现在 to_dict ⇒ 否则无法从心跳判断上限是否生效"
        "（F282/F289 的教训：改了但状态里看不出来）")
    assert isinstance(d["leg_capped"], int)
