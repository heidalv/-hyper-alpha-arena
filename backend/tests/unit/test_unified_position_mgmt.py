"""Phase B+C: 统一分段止盈 + 利润回撤 + 追踪 + 止盈安全网 单元测试.

直接测试 PaperTradingEngine._run_unified_staged_tp (ATR 自适应)。
覆盖:
  - TP1 触发 (+1.5×ATR) → 平 25% + SL 推到保本
  - TP3 触发 (+4.0×ATR) → 平 30% + 追踪止损激活
  - 利润硬回撤 (>4×ATR) → 全平
  - 止盈安全网 (PnL% > 80%) → 全平
  - regime 影响倍数 (trending vs extreme)
  - 状态持久化 (tp_level_reached / peak_pnl_pct)
"""
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# 确保可 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_pos(
    *,
    side: str = "long",
    entry: float = 100.0,
    size: float = 1.0,
    tp_level_reached: int = 0,
    peak_pnl_pct: float = 0.0,
    sl_price: float = 0.0,
    health_regime: str = "trending",
    atr_at_entry: float = 0.0,
):
    """构造一个轻量伪持仓对象 (只暴露统一块读取的属性)."""
    p = MagicMock()
    p.id = 1
    p.account_id = 1
    p.symbol = "TEST"
    p.side = side
    p.entry_price = entry
    p.size = size
    p.tp_level_reached = tp_level_reached
    p.peak_pnl_pct = peak_pnl_pct
    p.sl_price = sl_price
    p.health_regime = health_regime
    p.exit_state_json = None
    p.strategy_id = None
    p.atr_at_entry = atr_at_entry
    return p


def _make_engine():
    """构造 PaperTradingEngine 并把 DB / 数据池相关副作用打桩."""
    from backend.services.paper_trading_engine import PaperTradingEngine
    eng = PaperTradingEngine.__new__(PaperTradingEngine)
    # 统一块不读这些缓存, 但 _run_v2_protection 会; 这里只测 _run_unified_staged_tp
    eng._peak_profit_cache = {}
    eng._tp_levels_cache = {}
    eng._last_partial_close_at = {}
    eng._profit_manager = None
    # 记录对 close_position / _partial_close_by_pct 的调用
    eng._calls = []

    def _partial_side_effect(db, pos, pct, reason, *a, **kw):
        eng._calls.append({"reason": reason, "quantity": None, "full": False})
        return {"closed_fully": False, "pnl": 0.0}

    def _close_side_effect(db, account_id, symbol, side, reason="manual", *a, **kw):
        eng._calls.append({
            "reason": reason, "quantity": kw.get("quantity"),
            "full": kw.get("quantity") is None,
        })
        return {"closed_fully": True, "pnl": 0.0}

    eng._partial_close_by_pct = MagicMock(side_effect=_partial_side_effect)
    eng.close_position = MagicMock(side_effect=_close_side_effect)
    return eng


def _run(eng, pos, price, *, atr_pct=None, tp_cap=0.80, profit_pct=None):
    """便捷封装: 调用统一块. profit_pct 缺省由价格反推 (未杠杆 PnL%)."""
    entry = float(pos.entry_price)
    if profit_pct is None:
        if pos.side in ("long", "buy"):
            profit_pct = (price - entry) / entry
        else:
            profit_pct = (entry - price) / entry
    db = MagicMock()
    return eng._run_unified_staged_tp(
        db, pos, entry, price, profit_pct,
        atr_pct=atr_pct, tp_cap=tp_cap,
    )


# ────────────────────────── TP1 ──────────────────────────

def test_tp1_triggers_at_2_0_atr_closes_25pct_and_moves_sl_to_breakeven():
    """TP1: 价格达 +2.0×ATR (crypto-native 适配后档位) → 平 25%, SL → entry + ATR×0.8."""
    eng = _make_engine()
    # entry=100, ATR=2% → ATR 价格距离 = 2.0; TP1 触发价 = 100 × (1 + 0.02×2.0) = 104
    pos = _make_pos(entry=100.0, sl_price=95.0, health_regime="trending")

    closed = _run(eng, pos, price=104.0, atr_pct=0.02)

    assert closed is False, "TP1 是部分平仓, 不应全平"
    assert pos.tp_level_reached == 1, "tp_level_reached 应推进到 1"
    # 平仓调用: reason=staged_tp1
    partial_calls = [c for c in eng._calls if c["reason"] == "staged_tp1"]
    assert len(partial_calls) == 1, f"应有一次 staged_tp1 部分平仓, 实际: {eng._calls}"
    # SL 收紧到 entry + ATR×0.8 = 100 + 2.0×0.8 = 101.6
    # [2026-07-30 crypto-native] 0.3×ATR≈0.15% 太紧被 5m 正常波动击穿 → 0.8×ATR
    assert pos.sl_price == pytest.approx(101.6, abs=1e-6), \
        f"SL 应回到保本 101.6, 实际 {pos.sl_price}"


def test_tp1_does_not_trigger_below_threshold():
    """价格未达 +2.0×ATR 不应触发 TP1."""
    eng = _make_engine()
    pos = _make_pos(entry=100.0, sl_price=95.0, health_regime="trending")
    closed = _run(eng, pos, price=102.0, atr_pct=0.02)  # 仅 +1.0×ATR
    assert closed is False
    assert pos.tp_level_reached == 0
    assert eng._calls == [], "未达阈值不应有任何平仓"


# ────────────────────────── TP3 / trailing ──────────────────────────

def test_tp3_triggers_and_activates_trailing():
    """TP3: 价格达 +5.0×ATR (crypto-native 适配后档位) → 平 30%, 追踪止损激活."""
    eng = _make_engine()
    # entry=100, ATR=2% → TP3 触发价 = 100 × (1 + 0.02×5.0) = 110
    pos = _make_pos(entry=100.0, sl_price=95.0, health_regime="trending")

    closed = _run(eng, pos, price=110.0, atr_pct=0.02)

    assert closed is False
    assert pos.tp_level_reached == 3, "应直接跳到 TP3 (单 tick 最高档)"
    tp3_calls = [c for c in eng._calls if c["reason"] == "staged_tp3"]
    assert len(tp3_calls) == 1, f"应有一次 staged_tp3, 实际 {eng._calls}"
    # 峰值价 = 110 (当前价创新高); trailing SL = 110 - 100×0.02×2.0 = 110 - 4 = 106
    assert pos.sl_price == pytest.approx(106.0, abs=1e-6), \
        f"TP3 后追踪 SL 应为 106.0, 实际 {pos.sl_price}"


def test_trailing_only_tightens_after_tp3():
    """TP3 后下一 tick 价格更高 → 追踪 SL 应单调收紧 (不放宽)."""
    eng = _make_engine()
    pos = _make_pos(entry=100.0, sl_price=95.0, health_regime="trending")
    # 第一 tick: 触发 TP3, SL → 106
    _run(eng, pos, price=110.0, atr_pct=0.02)
    sl_after_tp3 = pos.sl_price
    assert sl_after_tp3 == pytest.approx(106.0, abs=1e-6)
    eng._calls.clear()
    # 第二 tick: 价格回落到 108 (peak 仍 110), 回撤 2 价 = 1×ATR < dd_hard, 不全平
    closed = _run(eng, pos, price=108.0, atr_pct=0.02)
    assert closed is False
    # SL 不应放宽 (peak=110, trail=110-4=106, 与之前相同)
    assert pos.sl_price == pytest.approx(106.0, abs=1e-6), \
        "追踪 SL 不应放宽"


# ────────────────────────── 利润回撤 ──────────────────────────

def test_hard_drawdown_closes_all():
    """利润硬回撤 > 4×ATR (任何阶段) → 全平 (profit_drawdown_hard)."""
    eng = _make_engine()
    # 先把 peak 抬高: entry=100, peak_pnl_pct=10% → peak_price=110
    pos = _make_pos(entry=100.0, peak_pnl_pct=0.10, health_regime="trending")
    # 当前价 101: 回撤 = (110-101)/100 / 0.02 = 9/2 = 4.5×ATR > 4.0
    closed = _run(eng, pos, price=101.0, atr_pct=0.02)
    assert closed is True, "硬回撤应全平"
    hard_calls = [c for c in eng._calls if c["reason"] == "profit_drawdown_hard"]
    assert len(hard_calls) == 1, f"应有 profit_drawdown_hard 全平, 实际 {eng._calls}"


def test_soft_drawdown_only_after_tp1():
    """软回撤 > 2×ATR 但 < 4×ATR: 仅在 TP1 后全平, 否则不止盈."""
    # 1) TP1 未触发 → 不应全平
    eng = _make_engine()
    pos = _make_pos(entry=100.0, peak_pnl_pct=0.06, tp_level_reached=0,
                    health_regime="trending")
    # peak=106, 当前价 101.5: 回撤=(106-101.5)/100/0.02 = 4.5/2 = 2.25×ATR (软区间)
    closed = _run(eng, pos, price=101.5, atr_pct=0.02)
    assert closed is False, "TP1 前软回撤不应全平"

    # 2) TP1 已触发 → 应全平
    eng2 = _make_engine()
    pos2 = _make_pos(entry=100.0, peak_pnl_pct=0.06, tp_level_reached=1,
                     health_regime="trending")
    closed2 = _run(eng2, pos2, price=101.5, atr_pct=0.02)
    assert closed2 is True, "TP1 后软回撤应全平"
    soft_calls = [c for c in eng2._calls if c["reason"] == "profit_drawdown_stage"]
    assert len(soft_calls) == 1


# ────────────────────────── 止盈安全网 ──────────────────────────

def test_tp_safety_net_cap_closes_all_at_80pct():
    """未杠杆 PnL% > 80% → 全平 (tp_safety_net_cap), 优先级最高."""
    eng = _make_engine()
    pos = _make_pos(entry=100.0, health_regime="trending")
    # 价格 181 → PnL% = 81% > 80% cap
    closed = _run(eng, pos, price=181.0, atr_pct=0.02, tp_cap=0.80)
    assert closed is True
    cap_calls = [c for c in eng._calls if c["reason"] == "tp_safety_net_cap"]
    assert len(cap_calls) == 1, f"安全网应优先全平, 实际 {eng._calls}"


# ────────────────────────── regime 影响 ──────────────────────────

def test_regime_extreme_raises_tp1_threshold_relative_to_trending():
    """extreme regime 的 tp1_mult(3.0) > trending(2.0), 同价格下 trending 触发而 extreme 不触发."""
    atr = 0.02
    # 价格 = entry × (1 + atr × 2.0) = 100 × 1.04 = 104
    # trending tp1_mult=2.0 → 2.0 >= 2.0 触发
    # extreme tp1_mult=3.0 → 2.0 < 3.0 不触发
    price = 100.0 * (1 + atr * 2.0)

    eng_t = _make_engine()
    pos_t = _make_pos(entry=100.0, health_regime="trending")
    _run(eng_t, pos_t, price=price, atr_pct=atr)
    assert pos_t.tp_level_reached >= 1, "trending 应在 +2.0×ATR 触发 TP1"

    eng_e = _make_engine()
    pos_e = _make_pos(entry=100.0, health_regime="extreme")
    _run(eng_e, pos_e, price=price, atr_pct=atr)
    assert pos_e.tp_level_reached == 0, "extreme 在 +2.0×ATR 不应触发 TP1 (需 3.0)"


def test_regime_params_match_design_spec():
    """REGIME_TP_PARAMS 三档倍数与设计文档 §2.3 一致.

    [2026-07-30 crypto-native 适配] 实现侧按 5m scalp 实测（breakeven_tp 100%）
    有意调整：tp1_mult 抬升到 2.0+，sl_mult 适度收窄，trail_mult 给呼吸空间。
    测试期望随实现同步，保留三档单调关系断言。
    """
    from backend.services.paper_trading_engine import PaperTradingEngine as E
    assert E.REGIME_TP_PARAMS["trending"] == {
        "sl_mult": 2.0, "tp1_mult": 2.0, "tp2_mult": 3.0, "tp3_mult": 5.0,
        "trail_mult": 2.0, "dd_hard": 4.0,
    }
    # 档位单调性：极端 > 震荡 > 趋势（tp1 至少不降，tp3 严格递增）
    _t, _r, _x = E.REGIME_TP_PARAMS["trending"], E.REGIME_TP_PARAMS["ranging"], E.REGIME_TP_PARAMS["extreme"]
    assert _r["tp1_mult"] > _t["tp1_mult"]
    assert _x["tp1_mult"] >= _r["tp1_mult"]
    assert _t["tp3_mult"] < _r["tp3_mult"] < _x["tp3_mult"]
    assert _t["sl_mult"] < _r["sl_mult"] < _x["sl_mult"]
    assert E._UNIFIED_TP_DEFAULT_PARAMS == E.REGIME_TP_PARAMS["trending"]


# ────────────────────────── 状态持久化 ──────────────────────────

def test_peak_pnl_pct_monotonically_increases():
    """盈利创新高时 peak_pnl_pct 单调推进 (跨 tick 稳定)."""
    eng = _make_engine()
    pos = _make_pos(entry=100.0, peak_pnl_pct=0.0, health_regime="trending")
    # 价格 103 (+3%) → peak_pnl_pct 应到 0.03
    _run(eng, pos, price=103.0, atr_pct=0.02)
    assert pos.peak_pnl_pct == pytest.approx(0.03, abs=1e-9)
    # 价格 105 (+5%) → peak_pnl_pct 推进到 0.05
    _run(eng, pos, price=105.0, atr_pct=0.02)
    assert pos.peak_pnl_pct == pytest.approx(0.05, abs=1e-9)
    # 价格回落 104 → peak_pnl_pct 不回退
    _run(eng, pos, price=104.0, atr_pct=0.02)
    assert pos.peak_pnl_pct == pytest.approx(0.05, abs=1e-9)


# ────────────────────────── 短仓 / 方向 ──────────────────────────

def test_short_side_tp1_and_breakeven_sl():
    """short 仓: 价格下跌 +2.0×ATR → TP1, SL → entry - ATR×0.8."""
    eng = _make_engine()
    pos = _make_pos(side="short", entry=100.0, sl_price=105.0, health_regime="trending")
    # short 盈利方向是价跌; ATR=2%, TP1 触发价 = 100 × (1 - 0.02×2.0) = 96
    closed = _run(eng, pos, price=96.0, atr_pct=0.02)
    assert closed is False
    assert pos.tp_level_reached == 1
    # short SL 收紧 = entry - ATR×0.8 = 100 - 2.0×0.8 = 98.4 (比 105 更小 = 更有利)
    assert pos.sl_price == pytest.approx(98.4, abs=1e-6), \
        f"short 保本 SL 应为 98.4, 实际 {pos.sl_price}"


def test_short_side_hard_drawdown_closes_all():
    """short 仓利润硬回撤同样全平."""
    eng = _make_engine()
    # short: peak_pnl_pct=10% → peak_price = 100×(1-0.10) = 90
    pos = _make_pos(side="short", entry=100.0, peak_pnl_pct=0.10, health_regime="trending")
    # 价格反弹到 99: short 回撤 = (99-90)/100 / 0.02 = 9/2 = 4.5×ATR > 4
    closed = _run(eng, pos, price=99.0, atr_pct=0.02)
    assert closed is True
    hard = [c for c in eng._calls if c["reason"] == "profit_drawdown_hard"]
    assert len(hard) == 1


# ────────────────────────── PEO 旁路 ──────────────────────────

def test_peo_staged_tp_bypassed_when_v2_unified_on(monkeypatch):
    """RISK_V2_UNIFIED_STAGED_TP=true 时, PEO 的 nature_staged_tp reduce 被旁路."""
    import backend.config.settings as S
    monkeypatch.setattr(S, "RISK_V2_UNIFIED_STAGED_TP", True)
    from backend.services.position_exit_orchestrator import PositionExitOrchestrator
    from backend.services.nature_staged_tp import NatureStagedTpDecision

    orch = PositionExitOrchestrator()

    # 伪造一个本应触发 reduce 的强盈利持仓
    pos_dict = {
        "id": 1, "symbol": "TEST", "side": "long",
        "entry_price": 100.0, "mark_price": 130.0,  # +30% 远超 staged 阈值
        "trade_nature": "swing", "timeframe_tier": "mid",
        "size": 1.0, "leverage": 1.0, "strategy_id": None,
    }
    db = MagicMock()
    db_pos = MagicMock()
    db_pos.id = 1
    db_pos.exit_state_json = None
    db_pos.peak_pnl_pct = 0.0
    db_pos.sl_price = 95.0
    db_pos.tp_price = None
    db_pos.opened_at = None
    db_pos.closed_at = None
    db.query.return_value.filter.return_value.first.return_value = db_pos

    # patch paper_engine.close_position 以观测是否被 PEO 调用做 staged 减仓
    from backend.services import paper_trading_engine as pte_mod
    close_calls = []
    def _fake_close(*a, **k):
        close_calls.append(k.get("reason") or (a[3] if len(a) > 3 else "?"))
        return None
    monkeypatch.setattr(pte_mod.paper_engine, "close_position", _fake_close)
    monkeypatch.setattr(pte_mod.paper_engine, "update_position_tp_sl", lambda *a, **k: None)

    changes = orch.evaluate_and_execute(
        db=db, account_id=1,
        positions=[pos_dict],
        market_summary={"TEST": {"volatility_value": 0.02}},
        session=None, append_event=None,
    )
    staged_closes = [r for r in close_calls if "staged" in str(r) or "nature_tp" in str(r)]
    assert staged_closes == [], \
        f"v2 统一接管时 PEO 不应发 staged TP 平仓, 实际: {close_calls}"


def test_constants_and_flag_exposed():
    """新加的配置 flag 与常量可被读取."""
    from backend.config.settings import RISK_V2_UNIFIED_STAGED_TP, RISK_V2_TP_SAFETY_NET_CAP
    assert isinstance(RISK_V2_UNIFIED_STAGED_TP, bool)
    assert 0 < RISK_V2_TP_SAFETY_NET_CAP <= 1.0
