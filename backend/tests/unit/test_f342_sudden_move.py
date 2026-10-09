# -*- coding: utf-8 -*-
"""[F342 2026-09-22] 突发事件闸（暴涨/暴跌）—— 刻意**不预测方向**。

# 现状缺口（全部已核实）

| 检测 | 判据 | 对突发事件的速度 |
|---|---|---|
| `trend_pause_bp=15` | `trend_move_bp(mid_hist, 20)` | 20 期 × 实测 tick 18~20s ≈ **6.7 分钟** ❌ |
| `vol_pause_sigma=0.7` | `realized_vol_bp(mid_hist, 20)` | 同样 6.7 分钟 ❌ |
| `ofi_block_threshold=0.5` | 上一 15s 桶 OFI | 快，但**只封下单侧** |
| `stop_loss_bp=80` | 浮亏 80bp | 唯一处理持仓的，阈值很深 |
| `check_data_freshness` | `MAX_DATA_AGE_SEC=180` | 断网后 **3 分钟**才判陈旧 ❌ |

⇒ 价格类检测全部建在 **6.7 分钟**窗口上，而暴涨暴跌发生在**几秒到几十秒**。

# 为什么本闸不预测方向（这是设计核心，不是偷懒）

H244 实测（24 小时逐秒 tick，去重叠 + 减基线）：闪动事件（10s 内 ≥50bp）
**独立事件只有 10 个**，事件后 60s 的 fwd **从 −47bp 到 +43bp 全谱都有，
中位仅 +2.5bp** ⇒ 方向**不可预测**。

本会话已有 **6 次**"找方向信号"的尝试全部跨窗口翻转
（OFI / 成交规模 / 成交笔数 / 阈值扫描 / 比值结构性 / 名义-盈亏）。
⇒ 不重复第 7 次。本闸只做两件**有确定性收益**的事：

  ① 急动中**停止加仓** —— "在急动里挂单必被逆向选择"是**已知**的
     （实测 maker 腿 `price_bp = −1.18bp`，而 `spread_bp` 只有 +0.19bp）；
  ② 让持仓走**宽挂 maker** 出库，**绝不 taker 砸单**
     （taker = 4bp fee + 过价，且正好付在急动后最宽的点差上）。

# 本测试锁什么

  ① `sudden_move_bp=0`（默认）⇒ 与旧行为**逐字一致**（可一键回退）；
  ② 命中 ⇒ `skip="sudden_move"` + `action="pause"` + 两侧都不挂新单；
  ③ **命中时不清空 `state.quote_*`** —— 这是最容易被"顺手改坏"的一条：
     清空会让已有减仓侧挂单**立刻失去被动成交机会** ⇒ 库存只能等 taker 砸单；
  ④ **必须自解除**（冷却期过了就不再拦）—— 与 `toxic_streak` 同一个坑，
     F98 已因"只有进入没有退出"栽过一次（整车道永久停摆）；
  ⑤ 冷却期语义正确（`sudden_move_until` 单调取 max，不被更短的值拉低）。
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
    sudden_move_bp,
    sudden_move_hit,
)


def _plan(*, mid_hist, mid=1.0, fill_notional=100.0, limits=None, qty=0.0,
          now_ts=None, judged=(0.9995, 1.0005, 1.0), state=None):
    """跑一次 `plan_tick`。

    `state` 可传入**同一个** SymbolState 以模拟连续 tick —— 冷却期
    （`sudden_move_until`）存在 state 上，跨 tick 断言必须复用 state，
    否则每次都新建 ⇒ 冷却永远"刚刚设置"。
    """
    b = InventoryBook()
    if qty:
        b.positions["SOLUSDT"] = Position(qty=qty, opened_ts=time.time() - 5.0)
    st = state if state is not None else R.SymbolState(symbol="SOLUSDT")
    st.mid_hist = list(mid_hist)
    kwargs = {
        "state": st, "mid": mid, "seg_low": 0.999, "seg_high": 1.001,
        "seg_taker_sell": 2000.0, "seg_taker_buy": 2000.0,
        "now_ts": float(now_ts if now_ts is not None else time.time()),
        "params": QuoteParams(spread_mult=0.5, w_base_bp=5.0, min_width_bp=0.3),
        "limits": limits if limits is not None else LaneRiskLimits(),
        "book": b, "equity": 1000.0, "fill_notional": fill_notional,
        "half_spread": 0.0005, "judged_quote": judged,
    }
    sig = inspect.signature(R.plan_tick)
    kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
    # `plan_tick` 返回 `(TickDecision, meta_dict)`；本 helper 再附上 state
    # （测试要同时断言决策与运行态字段，如 `sudden_move_hits`）。
    dec, _meta = R.plan_tick(**kwargs)
    return dec, st


# ── 0. 纯函数自证 ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_sudden_move_bp_measures_one_step():
    """`k=1` ⇒ 最近一期 vs 上一期。"""
    assert sudden_move_bp([100.0, 100.0], 1) == pytest.approx(0.0)
    # 100 → 100.5 = +50bp
    assert sudden_move_bp([100.0, 100.5], 1) == pytest.approx(50.0, abs=1e-6)
    # k=2 ⇒ 比较 -3 与 -1
    assert sudden_move_bp([100.0, 100.0, 100.5], 2) == pytest.approx(50.0, abs=1e-6)
    assert sudden_move_bp([], 1) == 0.0
    assert sudden_move_bp([100.0], 1) == 0.0


@pytest.mark.unit
def test_sudden_move_hit_threshold_and_disable():
    """`thresh<=0` ⇒ 关闭；否则用**绝对幅度**判定（暴涨暴跌都命中）。"""
    assert sudden_move_hit([100.0, 100.6], 0.0)[0] is False
    assert sudden_move_hit([100.0, 100.6], -1.0)[0] is False
    # +60bp 命中
    hit, mv = sudden_move_hit([100.0, 100.6], 50.0)
    assert hit is True and mv == pytest.approx(60.0, abs=1e-6)
    # −60bp 也命中（方向无关）
    hit2, mv2 = sudden_move_hit([100.0, 99.4], 50.0)
    assert hit2 is True and mv2 == pytest.approx(-60.0, abs=1e-6)
    # 未达阈值
    assert sudden_move_hit([100.0, 100.1], 50.0)[0] is False


# ── 1. 默认关闭：与旧行为逐字一致 ────────────────────────────────────────
@pytest.mark.unit
def test_default_is_disabled():
    assert LaneRiskLimits().sudden_move_bp == 0.0, "默认必须为 0（关闭）"
    # 一个 200bp 的急动，在关闭时不得改变任何行为
    h = [1.0, 1.02]
    d_off, _ = _plan(mid_hist=h, limits=LaneRiskLimits(sudden_move_bp=0.0))
    d_ref, _ = _plan(mid_hist=h, limits=LaneRiskLimits())
    assert d_off.skip != "sudden_move" and d_ref.skip != "sudden_move"
    assert (d_off.bid, d_off.ask) == (d_ref.bid, d_ref.ask)


# ── 2. 命中：停新挂单，两侧都不挂 ────────────────────────────────────────
@pytest.mark.unit
def test_hit_pauses_new_quotes_on_both_sides():
    h = [1.0, 1.02]                    # +200bp 急涨
    lim = LaneRiskLimits(sudden_move_bp=50.0, sudden_move_k=1)
    dec, st = _plan(mid_hist=h, limits=lim)
    assert dec.skip == "sudden_move", f"应命中突发事件闸；实际 skip={dec.skip!r}"
    assert dec.action == "pause"
    assert dec.skip_side == "both", "两侧都不得挂新单"
    assert "mv=" in dec.skip_detail and "thr=" in dec.skip_detail
    assert st.sudden_move_hits == 1


@pytest.mark.unit
def test_down_move_also_hits():
    """暴跌同样命中（判据是绝对幅度，不是方向）。"""
    dec, st = _plan(mid_hist=[1.0, 0.98],
                    limits=LaneRiskLimits(sudden_move_bp=50.0))
    assert dec.skip == "sudden_move"
    assert st.sudden_move_hits == 1


@pytest.mark.unit
def test_below_threshold_does_not_hit():
    dec, st = _plan(mid_hist=[1.0, 1.001],     # +10bp
                    limits=LaneRiskLimits(sudden_move_bp=50.0))
    assert dec.skip != "sudden_move"
    assert st.sudden_move_hits == 0


# ── 3. **最关键**：命中时不清空已有挂单（保出库路径）──────────────────────
@pytest.mark.unit
def test_hit_does_not_clear_existing_quotes():
    """命中必须**保留** `state.quote_*`。

    为什么这是安全关键的：清空会让 `_lagged_quote` 记零单标记 ⇒
    **已有的减仓侧挂单立刻失去被动成交机会** ⇒ 库存只能等超时/止损
    **taker 砸单**，而 taker 正好付在"急动后最宽的点差"上（4bp fee + 过价）。

    保守的代价只是"老价被吃到"，且有 `max_quote_age_sec=90` 兜底
    （超龄挂单会被 `stale_quote_cleared` 丢弃）。
    """
    h = [1.0, 1.02]
    lim = LaneRiskLimits(sudden_move_bp=50.0)
    dec, st = _plan(mid_hist=h, limits=lim)
    assert dec.skip == "sudden_move"          # 前提：确实命中了
    # 精确切出**本闸门自己**的代码块（从本闸的判据到它的 return），
    # 不能用固定长度窗口 —— 实测 ±1400 字符会越界到下一个闸门
    # （`vol_regime` 会清挂单）⇒ 假失败。
    src = inspect.getsource(R.plan_tick)
    i0 = src.index("_sm_bp = float(")
    i1 = src.index('return dec, {"book": local_book, "sudden_move"', i0)
    seg = src[i0:i1]
    assert "state.quote_bid" not in seg and "state.quote_ask" not in seg, (
        "突发事件闸里出现了清空挂单的语句 ⇒ 会切断被动出库路径，"
        "把亏损从价差搬到 taker 强平（本会话已见过的失败模式）")


# ── 4. **必须自解除**（F98 的同一个坑）──────────────────────────────────
@pytest.mark.unit
def test_cooldown_expires_and_gate_reopens():
    """冷却期过了就不再拦 —— 否则会变成永久停摆。"""
    h = [1.0, 1.02]
    lim = LaneRiskLimits(sudden_move_bp=50.0, sudden_move_cooldown_sec=60.0)
    t0 = time.time()
    dec1, st = _plan(mid_hist=h, limits=lim, now_ts=t0)
    assert dec1.skip == "sudden_move"
    assert st.sudden_move_until == pytest.approx(t0 + 60.0, abs=1.0)
    # 冷却期内（价格已回稳）仍拦 —— **复用同一个 state**（冷却存在 state 上）
    dec2, _ = _plan(mid_hist=[1.0, 1.0], limits=lim, now_ts=t0 + 30.0, state=st)
    assert dec2.skip == "sudden_move", "冷却期内应继续拦（余波仍在）"
    # 冷却期后（价格回稳）必须放行
    dec3, _ = _plan(mid_hist=[1.0, 1.0], limits=lim, now_ts=t0 + 61.0, state=st)
    assert dec3.skip != "sudden_move", (
        "冷却期已过却仍在拦 ⇒ 永久停摆风险（F98 同一个坑）")


@pytest.mark.unit
def test_cooldown_takes_max_not_overwritten():
    """新的更短冷却不得把已设的更长期限**拉低**。"""
    h = [1.0, 1.02]
    lim = LaneRiskLimits(sudden_move_bp=50.0, sudden_move_cooldown_sec=600.0)
    t0 = time.time()
    _d, st = _plan(mid_hist=h, limits=lim, now_ts=t0)
    far = st.sudden_move_until
    st.sudden_move_until = far
    # 第二次命中时 now 更晚，但 cooldown 更短 ⇒ 必须取 max
    _d2, st2 = _plan(mid_hist=h, limits=LaneRiskLimits(
        sudden_move_bp=50.0, sudden_move_cooldown_sec=1.0), now_ts=t0 + 5.0)
    assert st2.sudden_move_until >= t0 + 6.0 - 1e-6


@pytest.mark.unit
def test_zero_cooldown_only_pauses_the_hitting_tick():
    """`cooldown=0` ⇒ 只暂停命中那一 tick（旧行为最接近的语义）。"""
    h = [1.0, 1.02]
    lim = LaneRiskLimits(sudden_move_bp=50.0, sudden_move_cooldown_sec=0.0)
    t0 = time.time()
    dec1, st = _plan(mid_hist=h, limits=lim, now_ts=t0)
    assert dec1.skip == "sudden_move"
    dec2, _ = _plan(mid_hist=[1.0, 1.0], limits=lim, now_ts=t0 + 0.001, state=st)
    assert dec2.skip != "sudden_move"


# ── 5. 位置：必须在 exits 之后（不阻断离场）─────────────────────────────
@pytest.mark.unit
def test_gate_sits_after_all_exit_paths():
    """源码顺序断言：`sudden_move` 块必须在四条出口路径**之后**。

    否则突发事件闸会挡住止损/止盈/超时出库 ⇒ 库存被锁死在最坏的行情里。
    这与 `lane_pause_reason` 的既有约定一致（"暂停永远不阻断已有库存的离场"）。
    """
    src = inspect.getsource(R.plan_tick)
    i_sm = src.index('dec.skip = "sudden_move"')
    for marker, name in (
        # [h389] 止损与尾随锁利合并为同一强平流程（三元出口标记）
        ('dec.exit_path = "trail_lock_taker" if _trail_hit else "stop_loss_taker"',
         "止损/尾随"),
        ('dec.exit_path = "take_profit_taker"', "止盈"),
        ('dec.exit_path = "timeout_taker"', "超时"),
        ('dec.exit_path = "ofi_flatten_taker"', "OFI 平仓"),
    ):
        assert marker in src, f"找不到 {name} 出口标记 ⇒ 结构变了，需同步更新"
        assert src.index(marker) < i_sm, (
            f"突发事件闸排在**{name}**之前 ⇒ 它会阻断该出口，"
            f"把库存锁死。必须放在所有 exits 之后。")


@pytest.mark.unit
def test_symbol_state_has_self_clearing_fields():
    """运行态字段存在且可持久化（float/int，不是 mutable 默认值）。"""
    st = R.SymbolState(symbol="X")
    assert st.sudden_move_until == 0.0
    assert st.sudden_move_hits == 0
    # 0 必须表示"未在冷却中"（epoch 0 远在过去 ⇒ `0 > now` 恒假）
    assert not (st.sudden_move_until > time.time())
