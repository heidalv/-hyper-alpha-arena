"""PostFill Position Agent（成交后持仓管理）Phase 1-3 验收测试 · 2026-08-30。

覆盖：
1. feasibility_gate：minNotional 双边检查（ok/reject/escalate_full）+ 费用预算门
2. 引擎 close_position 的 minNotional 门（拒单 / 升级全平）
3. 统一分段止盈名义额降档（单档/两档/三档 + 档位不消费）
4. MAE 谷值同步（_sync_peak_state 对称下探）
5. 状态机 sync_position_state 对齐 + profit_drawdown 兜底收口门控
6. scalp 持仓复审纯函数（ATR/保本判定/持仓时长）
7. live reduce_sub_position 的 minNotional 门（拒单 / 升级全平）
8. [2026-08-31] live 真实减仓路径 close_sub_position 的 minNotional 门
9. [2026-08-31] 费用预算门按账户交易所实际费率（修复硬编码 0.0005）
10. [2026-08-31] 延长持仓 4 条硬条件（peak≥1R / 未破追踪线 / regime / funding<20%）
11. [2026-08-31] 硬线全平补登 ExitSource 事件（§3.5 账实分离）
12. [2026-08-31] 影子遥测（TP 降档 counterfactual / observe_only 开关解析；延长拒绝·放行事件断言在 §10 用例内）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.services.exit.feasibility_gate import (
    check_partial_close_notional,
    fee_budget_exceeded,
    resolve_min_notional_usd,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_qaa_memory(monkeypatch):
    """隔离 QAA/chroma 原生依赖。

    close_position → _write_retrospective → ingest_trade_lesson 链会打开
    chroma PersistentClient（共享 ./qaa_chromadb 目录）；测试环境残留锁/
    并发写会让原生 upsert 挂起（Python 层 try/except 无法捕获原生挂起）。
    单元测试只验引擎逻辑，直接 stub 桥接函数（仓库同款做法见
    test_qaa_integration.py 的隔离 fixture）。
    """
    try:
        import backend.services.qaa_trade_memory_bridge as _qaa_bridge
        monkeypatch.setattr(_qaa_bridge, "ingest_trade_lesson", lambda **kw: [])
        monkeypatch.setattr(
            _qaa_bridge, "ingest_trade_outcome",
            lambda outcome, *, source="": [],
        )
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════
# 1. feasibility_gate
# ════════════════════════════════════════════════════════════════════
class TestFeasibilityGate:
    def test_resolve_by_exchange(self):
        assert resolve_min_notional_usd("hyperliquid") == 10.0
        assert resolve_min_notional_usd("binance") == 5.0

    def test_resolve_override(self, monkeypatch):
        monkeypatch.setenv("EXIT_MIN_NOTIONAL_OVERRIDE_USD", "20")
        assert resolve_min_notional_usd("binance") == 20.0

    def test_verdict_ok(self):
        v, d = check_partial_close_notional(
            exchange="binance", chunk_qty=0.5, pos_qty=1.0, price=100.0)
        assert v == "ok"  # chunk $50, remaining $50, min $5

    def test_verdict_reject_chunk_too_small(self):
        v, d = check_partial_close_notional(
            exchange="hyperliquid", chunk_qty=0.05, pos_qty=1.0, price=100.0)
        assert v == "reject"  # chunk $5 < $10

    def test_verdict_escalate_remaining_dust(self):
        v, d = check_partial_close_notional(
            exchange="binance", chunk_qty=0.98, pos_qty=1.0, price=100.0)
        assert v == "escalate_full"  # chunk ok, remaining $2 < $5

    def test_verdict_unknown_on_bad_price(self):
        v, _ = check_partial_close_notional(
            exchange="binance", chunk_qty=1.0, pos_qty=2.0, price=0.0)
        assert v == "unknown"

    def test_fee_budget(self):
        ok, _ = fee_budget_exceeded(fees_paid=0.1, est_leg_fee=0.1, notional_now=100.0)
        assert not ok  # 0.2%
        bad, detail = fee_budget_exceeded(fees_paid=10.0, est_leg_fee=1.0, notional_now=50.0)
        assert bad  # 22% > 15%

    def test_fee_budget_disabled(self, monkeypatch):
        monkeypatch.setenv("EXIT_FEE_BUDGET_PCT", "0")
        bad, _ = fee_budget_exceeded(fees_paid=99.0, est_leg_fee=1.0, notional_now=10.0)
        assert not bad

    def test_unknown_exchange_falls_back_to_binance(self):
        # [2026-08-31] 规则表回退交易所=币安：未知/空交易所不再回退已停用的
        # hyperliquid（$10 门槛），统一按币安 $5。
        assert resolve_min_notional_usd("unknown_venue") == 5.0
        assert resolve_min_notional_usd("") == 5.0
        assert resolve_min_notional_usd(None) == 5.0

    def test_explicit_exchanges_unaffected(self):
        # 显式配置不受回退影响：HL $10 / 币安 $5
        assert resolve_min_notional_usd("hyperliquid") == 10.0
        assert resolve_min_notional_usd("binance") == 5.0


# ════════════════════════════════════════════════════════════════════
# 2. 引擎 minNotional 门（close_position 集成）
# ════════════════════════════════════════════════════════════════════
class TestEngineMinNotionalGate:
    @pytest.fixture()
    def make_pos(self, db_session):
        """工厂：每个用例独立 account_id（paper_balances.account_id 唯一约束）。"""
        from backend.database.models import PaperBalance, PaperPosition

        def _make(acct_id: int, size: float):
            db_session.add(PaperBalance(
                account_id=acct_id, available_balance=10000.0,
                total_equity=10000.0, realized_pnl=0.0,
            ))
            pos = PaperPosition(
                account_id=acct_id, symbol="BTC", side="long",
                size=size, entry_price=100.0, mark_price=100.0,
                leverage=5, margin=2.0, status="open",
                trade_nature="scalp", timeframe_tier="short",
            )
            db_session.add(pos)
            db_session.commit()
            return pos
        return _make

    def _patch_engine(self, monkeypatch):
        from backend.services.paper_trading_engine import paper_engine
        monkeypatch.setattr(paper_engine, "_resolve_account_exchange",
                            lambda self, db, aid=None: "hyperliquid")
        monkeypatch.setattr(paper_engine, "_get_current_price",
                            lambda self, sym, ex=None: 100.0)
        return paper_engine

    def test_partial_below_min_rejected(self, db_session, make_pos, monkeypatch):
        eng = self._patch_engine(monkeypatch)
        pos = make_pos(7701, 0.10)
        # hyperliquid min $10：平 0.05 个（$5）→ 拒单返回 None，仓位不动
        res = eng.close_position(
            db_session, 7701, "BTC", "long", reason="staged_tp1", quantity=0.05)
        assert res is None
        db_session.refresh(pos)
        assert float(pos.size) == pytest.approx(0.10)
        assert pos.status == "open"

    def test_partial_remaining_dust_escalates_full(self, db_session, make_pos, monkeypatch):
        eng = self._patch_engine(monkeypatch)
        pos = make_pos(7702, 0.20)
        # 平 0.15 个（$15 ≥ min$10 ok），剩余 0.05（$5 < $10）→ 升级全平
        res = eng.close_position(
            db_session, 7702, "BTC", "long", reason="staged_tp1", quantity=0.15)
        assert res is not None
        assert res.get("closed_fully") is True
        db_session.refresh(pos)
        assert pos.status == "closed"

    def test_feasible_partial_passes(self, db_session, make_pos, monkeypatch):
        eng = self._patch_engine(monkeypatch)
        pos = make_pos(7703, 1.0)  # $100 名义
        res = eng.close_position(
            db_session, 7703, "BTC", "long", reason="staged_tp1", quantity=0.5)
        assert res is not None
        assert res.get("closed_fully") is False
        assert res.get("remaining_size") == pytest.approx(0.5)


# ════════════════════════════════════════════════════════════════════
# 3. 统一分段止盈：名义额自动降档 + 档位不消费
# ════════════════════════════════════════════════════════════════════
def _fake_pos(size, price=100.0, atr_entry=1.0, tp_level=0, side="long"):
    return SimpleNamespace(
        id=42, account_id=777, symbol="BTC", side=side, size=size,
        entry_price=price, mark_price=price, atr_at_entry=atr_entry,
        peak_pnl_pct=0.0, tp_level_reached=tp_level, sl_price=0.0,
        strategy_id="s1", trade_nature="scalp", timeframe_tier="short",
        health_regime="trending", partial_fee_paid=0.0, status="open",
    )


class TestStagedTpLadderDowngrade:
    @pytest.fixture()
    def eng(self, monkeypatch):
        from backend.services.paper_trading_engine import paper_engine
        calls = {"closes": [], "partials": []}
        # 实例属性 mock：调用时不注入 self
        monkeypatch.setattr(
            paper_engine, "close_position",
            lambda db, aid, sym, side, reason="manual", **kw: (
                calls["closes"].append(reason) or {"closed_fully": True, "pnl": 0}
            ),
        )
        monkeypatch.setattr(
            paper_engine, "_partial_close_by_pct",
            lambda db, pos, pct, reason: (
                calls["partials"].append((pct, reason)) or
                {"closed_fully": False, "pnl": 0}
            ),
        )
        return paper_engine, calls

    def test_single_tier_full_close(self, eng):
        engine, calls = eng
        pos = _fake_pos(size=0.20)  # $20 名义 < $30 → 单档
        # ATR 1% → tp1_mult 2.0(trending) → 价格 +2% 即触发
        done = engine._run_unified_staged_tp(
            None, pos, 100.0, 102.5, 0.025)
        assert done is True
        assert calls["closes"] == ["staged_tp1_single"]
        assert calls["partials"] == []

    def test_two_tier_tp1_partial_then_tp2_clear(self, eng):
        engine, calls = eng
        pos = _fake_pos(size=0.60)  # $60 名义 ∈ [$30,$100) → 两档
        # 未到 tp2（3.0 ATR=3%），先到 tp1（2%）→ 平 40%
        done = engine._run_unified_staged_tp(None, pos, 100.0, 102.5, 0.025)
        assert done is False
        assert calls["partials"] == [(0.40, "staged_tp1")]
        assert pos.tp_level_reached == 1
        # TP2 触发（3%）→ 清仓
        pos2 = _fake_pos(size=0.60, tp_level=1)
        done2 = engine._run_unified_staged_tp(None, pos2, 100.0, 103.5, 0.035)
        assert done2 is True
        assert calls["closes"] == ["staged_tp2_clear"]

    def test_three_tier_unchanged_for_large(self, eng):
        engine, calls = eng
        pos = _fake_pos(size=3.0)  # $300 名义 → 三档
        done = engine._run_unified_staged_tp(None, pos, 100.0, 102.5, 0.025)
        assert done is False
        assert calls["partials"] == [(0.25, "staged_tp1")]
        assert pos.tp_level_reached == 1

    def test_rejected_partial_does_not_consume_level(self, eng, monkeypatch):
        engine, calls = eng
        monkeypatch.setattr(
            engine, "_partial_close_by_pct",
            lambda db, pos, pct, reason: None,  # 可行性门拦截
        )
        pos = _fake_pos(size=3.0)
        done = engine._run_unified_staged_tp(None, pos, 100.0, 102.5, 0.025)
        assert done is False
        assert pos.tp_level_reached == 0  # 档位不消费


# ════════════════════════════════════════════════════════════════════
# 4. MAE 谷值同步
# ════════════════════════════════════════════════════════════════════
class TestTroughSync:
    def test_trough_tracks_down_then_holds(self):
        from backend.services.paper_trading_engine import PaperTradingEngine
        pos = _fake_pos(size=1.0)
        pos.peak_unrealized_pnl = 0.0
        pos.peak_pnl_pct = 0.0
        pos.trough_pnl_pct = 0.0
        pos.trough_unrealized_pnl = 0.0
        eng = PaperTradingEngine()
        eng._sync_peak_state(pos, -5.0, 95.0)   # -5% 谷
        assert pos.trough_pnl_pct == pytest.approx(-0.05)
        assert pos.trough_unrealized_pnl == pytest.approx(-5.0)
        eng._sync_peak_state(pos, 10.0, 110.0)  # 反弹不抬谷、创新高推峰
        assert pos.trough_pnl_pct == pytest.approx(-0.05)
        assert pos.peak_pnl_pct == pytest.approx(0.10)


# ════════════════════════════════════════════════════════════════════
# 5. 状态机：sync + 回撤兜底收口
# ════════════════════════════════════════════════════════════════════
class TestStateMachinePostFill:
    def test_sync_position_state(self):
        from backend.services.exit.unified_exit_state_machine import exit_state_machine
        exit_state_machine.sync_position_state(
            987654, tp_level_reached=2, breakeven_active=True, peak_pnl_pct=4.0)
        st = exit_state_machine._get_state(987654)
        assert st.tp_level_reached == 2
        assert st.breakeven_active is True
        assert st.peak_pnl_pct == 4.0
        exit_state_machine.reset_position(987654)

    def test_drawdown_fallback_gated_default(self):
        """默认（单实现收口）：SM 的 % 口径 profit_drawdown 兜底不再触发。"""
        from backend.services.exit.exit_types import ExitRequest, PositionContext
        from backend.services.exit.unified_exit_state_machine import exit_state_machine
        # mid：pnl 1.2%（< breakeven 3%、< staged 6%）、趋势同向 → 策略层无动作；
        # peak 5% 回撤 76% ≥ 50% → 旧逻辑会 REDUCE(profit_drawdown)
        ctx = PositionContext(
            position_id=987001, symbol="BTC", tier="mid", side="long",
            entry_price=100, current_price=101.2, quantity=1.0,
            unrealized_pnl_pct=1.2, peak_pnl_pct=5.0, hold_seconds=999999,
            atr_pct=1.0, trend_4h_aligned=True, trend_1d_aligned=True,
        )
        req = ExitRequest(
            position_id=987001, symbol="BTC", tier="mid", source="hold_review",
            proposed_action="hold", urgency="NORMAL",
        )
        d = exit_state_machine.submit(req, ctx)
        assert d.source != "profit_drawdown"
        exit_state_machine.reset_position(987001)

    def test_drawdown_fallback_restorable(self, monkeypatch):
        monkeypatch.setenv("EXIT_DRAWDOWN_SINGLE_IMPL", "false")
        from backend.services.exit.exit_types import ExitRequest, PositionContext
        from backend.services.exit.unified_exit_state_machine import exit_state_machine
        ctx = PositionContext(
            position_id=987002, symbol="BTC", tier="mid", side="long",
            entry_price=100, current_price=101.2, quantity=1.0,
            unrealized_pnl_pct=1.2, peak_pnl_pct=5.0, hold_seconds=999999,
            atr_pct=1.0, trend_4h_aligned=True, trend_1d_aligned=True,
        )
        req = ExitRequest(
            position_id=987002, symbol="BTC", tier="mid", source="hold_review",
            proposed_action="hold", urgency="NORMAL",
        )
        d = exit_state_machine.submit(req, ctx)
        assert d.source == "profit_drawdown"
        exit_state_machine.reset_position(987002)


# ════════════════════════════════════════════════════════════════════
# 6. scalp 持仓复审纯函数
# ════════════════════════════════════════════════════════════════════
class TestScalpReviewHelpers:
    def test_atr_from_klines(self):
        import pandas as pd
        from backend.services.full_auto.scalp_position_review import _atr_pct_from_md
        rows = []
        px = 100.0
        for i in range(30):
            o = px
            c = px * (1.01 if i % 2 == 0 else 0.99)
            rows.append({"high": max(o, c) * 1.005, "low": min(o, c) * 0.995,
                         "close": c, "volume": 10})
            px = c
        atr = _atr_pct_from_md({"klines": pd.DataFrame(rows)})
        assert 0.3 < atr < 5.0  # 合理区间（默认兜底 0.8，真实值应偏离且合理）

    def test_breakeven_reached(self):
        from backend.services.full_auto.scalp_position_review import _breakeven_reached
        long_be = _fake_pos(size=1.0)
        long_be.side = "long"
        long_be.sl_price = 100.5
        long_be.entry_price = 100.0
        assert _breakeven_reached(long_be) is True
        long_be.sl_price = 99.0
        assert _breakeven_reached(long_be) is False

    def test_hold_seconds_naive(self):
        from backend.services.full_auto.scalp_position_review import _hold_seconds
        pos = _fake_pos(size=1.0)
        pos.opened_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert 590 <= _hold_seconds(pos) <= 650


# ════════════════════════════════════════════════════════════════════
# 7. live reduce_sub_position 的 minNotional 门
# ════════════════════════════════════════════════════════════════════
class TestLpmReduceGate:
    @pytest.fixture()
    def make_lpm_sub(self, db_session):
        """工厂：每个用例独立 account_id（避免用例间残留叠加）。"""
        from backend.database.models import LiveSubPosition

        def _make(acct_id: int):
            sub = LiveSubPosition(
                account_id=acct_id, symbol="BTC", side="long", trade_nature="scalp",
                timeframe_tier="short", size=0.30, leverage=5.0, margin=0.06,
                entry_price=100.0, status="open",
            )
            db_session.add(sub)
            db_session.commit()
            return sub
        return _make

    def _lpm(self):
        from backend.services.live_position_manager import LivePositionManager
        return LivePositionManager()

    def test_chunk_below_min_rejected(self, db_session, make_lpm_sub, monkeypatch):
        monkeypatch.setenv("EXIT_MIN_NOTIONAL_OVERRIDE_USD", "10")
        make_lpm_sub(8811)
        sent = []

        def fake_cb(db, sym, side, qty, lev):
            sent.append(qty)
            return {"order_id": "x1", "fill_price": 100.0}

        res = self._lpm().reduce_sub_position(
            db_session, 8811, "BTC", "scalp", 0.10, fake_cb)  # chunk $3 < $10
        assert res["reduced"] is False
        assert "min_notional" in res["reason"]
        assert sent == []  # 未发单

    def test_remaining_dust_escalates_full(self, db_session, make_lpm_sub, monkeypatch):
        monkeypatch.setenv("EXIT_MIN_NOTIONAL_OVERRIDE_USD", "10")
        make_lpm_sub(8812)
        sent = []

        def fake_cb(db, sym, side, qty, lev):
            sent.append(qty)
            return {"order_id": "x2", "fill_price": 100.0}

        res = self._lpm().reduce_sub_position(
            db_session, 8812, "BTC", "scalp", 0.90, fake_cb)  # 剩余 $3 < $10
        assert res["reduced"] is True
        assert sent == [pytest.approx(0.30)]  # 升级为全平 0.30
        assert res["remain_qty"] == pytest.approx(0.0)


# ════════════════════════════════════════════════════════════════════
# 8. live 真实减仓路径（close_sub_position）的 minNotional 门
#    [2026-08-31 修正：此前门只挂在无调用方的 reduce_sub_position 上，
#     真实 live 减仓经 LiveExecutor.close_position → LPM 完全无门]
# ════════════════════════════════════════════════════════════════════
class TestLpmClosePathGate:
    @pytest.fixture()
    def make_lpm_sub(self, db_session):
        from backend.database.models import LiveSubPosition

        def _make(acct_id: int, size: float):
            sub = LiveSubPosition(
                account_id=acct_id, symbol="BTC", side="long", trade_nature="scalp",
                timeframe_tier="short", size=size, leverage=5.0, margin=size / 5.0,
                entry_price=100.0, status="open",
            )
            db_session.add(sub)
            db_session.commit()
            return sub
        return _make

    def _lpm(self):
        from backend.services.live_position_manager import LivePositionManager
        return LivePositionManager()

    def test_partial_below_min_rejected_no_order(self, db_session, make_lpm_sub, monkeypatch):
        monkeypatch.setenv("EXIT_MIN_NOTIONAL_OVERRIDE_USD", "10")
        make_lpm_sub(9911, 0.10)  # $10 名义
        sent = []

        def fake_cb(db, sym, side, qty, lev):
            sent.append(qty)
            return {"order_id": "c1", "fill_price": 100.0}

        res = self._lpm().close_sub_position(
            db_session, 9911, "BTC", "scalp", fake_cb, qty=0.05)  # 平 $5 < $10
        assert res["closed"] is False
        assert "min_notional" in res["reason"]
        assert sent == []  # 未发单
        from backend.database.models import LiveSubPosition
        sub = db_session.query(LiveSubPosition).filter(
            LiveSubPosition.account_id == 9911).first()
        assert float(sub.size) == pytest.approx(0.10)  # 账本不动

    def test_dust_remaining_escalates_full(self, db_session, make_lpm_sub, monkeypatch):
        monkeypatch.setenv("EXIT_MIN_NOTIONAL_OVERRIDE_USD", "10")
        make_lpm_sub(9912, 0.20)  # $20 名义
        sent = []

        def fake_cb(db, sym, side, qty, lev):
            sent.append(qty)
            return {"order_id": "c2", "fill_price": 100.0}

        res = self._lpm().close_sub_position(
            db_session, 9912, "BTC", "scalp", fake_cb, qty=0.15)  # 剩 $5 < $10
        assert res["closed"] is True
        assert sent == [pytest.approx(0.20)]  # 升级全平 0.20


# ════════════════════════════════════════════════════════════════════
# 9. 费用预算门按账户交易所实际费率（原硬编码 0.0005 修正）
# ════════════════════════════════════════════════════════════════════
class TestFeeBudgetUsesExchangeRate:
    def test_high_rate_blocks_partial_close(self, db_session, monkeypatch):
        from backend.database.models import PaperBalance, PaperPosition
        db_session.add(PaperBalance(
            account_id=8810, available_balance=10000.0,
            total_equity=10000.0, realized_pnl=0.0,
        ))
        pos = PaperPosition(
            account_id=8810, symbol="BTC", side="long", size=1.0,
            entry_price=100.0, mark_price=100.0, leverage=5, margin=2.0,
            status="open", trade_nature="scalp", timeframe_tier="short",
            partial_fee_paid=0.0,
        )
        db_session.add(pos)
        db_session.commit()

        from backend.services.paper_trading_engine import paper_engine
        monkeypatch.setattr(
            paper_engine, "_resolve_account_exchange",
            lambda self, db, aid=None: "asterdex")
        monkeypatch.setattr(
            paper_engine, "_get_current_price",
            lambda self, sym, ex=None: 100.0)
        import backend.services.fee_schedule_service as fss
        monkeypatch.setattr(
            fss, "get_fee_rate", lambda exchange, is_maker: 0.4)  # 40% taker

        # 腿费 = 0.5×100×0.4 = $20 → 20% > 15% 预算 → 拦截（不按 0.05% 硬编码放行）
        res = paper_engine._partial_close_by_pct(db_session, pos, 0.5, "staged_tp1")
        assert res is None
        db_session.refresh(pos)
        assert pos.status == "open"


# ════════════════════════════════════════════════════════════════════
# 10. 延长持仓 4 条硬条件（设计 §3.4.c 补齐）
# ════════════════════════════════════════════════════════════════════
class TestExtendHoldHardGate:
    @pytest.fixture()
    def make_mid_pos(self, db_session):
        from backend.database.models import PaperBalance, PaperPosition

        def _make(acct_id: int, **kw):
            db_session.add(PaperBalance(
                account_id=acct_id, available_balance=10000.0,
                total_equity=10000.0, realized_pnl=0.0,
            ))
            defaults = dict(
                account_id=acct_id, symbol="BTC", side="long", size=0.1,
                entry_price=100.0, mark_price=100.0, leverage=5, margin=0.2,
                status="open", trade_nature="trend_follow", timeframe_tier="mid",
                expected_hold_hours=12.0,
                opened_at=datetime.now(timezone.utc) - timedelta(hours=2),
                sl_price=95.0, peak_unrealized_pnl=0.0, unrealized_pnl=0.0,
            )
            defaults.update(kw)
            pos = PaperPosition(**defaults)
            db_session.add(pos)
            db_session.commit()
            return pos
        return _make

    def _patch(self, monkeypatch):
        from backend.services.paper_trading_engine import paper_engine
        monkeypatch.setattr(
            paper_engine, "_resolve_account_exchange",
            lambda self, db, aid=None: "binance")
        monkeypatch.setattr(
            paper_engine, "_get_current_price",
            lambda self, sym, ex=None: 100.0)
        # 固定 hold_time 依赖，隔离 runtime_tuning/pace 环境差异
        import backend.services.position_hold_time as pht
        monkeypatch.setattr(
            pht, "get_position_hold_status",
            lambda pos: {"max_hold_hours": 12.0})
        monkeypatch.setattr(
            pht, "resolve_tier_absolute_cap_seconds",
            lambda pos: 96 * 3600)
        return paper_engine

    def test_reject_when_peak_below_1r(self, db_session, make_mid_pos, monkeypatch):
        from backend.database.models import PositionExitEvent
        eng = self._patch(monkeypatch)
        pos = make_mid_pos(8821)  # risk = |100-95|×0.1 = 0.5，peak=0 < 1R
        res = eng.extend_position_hold_hours(db_session, pos.id, 4.0)
        assert res is None
        db_session.refresh(pos)
        assert pos.expected_hold_hours == pytest.approx(12.0)  # 未变更
        # [§3.7] 拒绝提案落影子遥测
        ev = db_session.query(PositionExitEvent).filter(
            PositionExitEvent.event_type == "postfill_telemetry_extend_rejected",
        ).first()
        assert ev is not None

    def test_allow_when_conditions_met(self, db_session, make_mid_pos, monkeypatch):
        from backend.database.models import PositionExitEvent
        eng = self._patch(monkeypatch)
        pos = make_mid_pos(8822, peak_unrealized_pnl=1.0, unrealized_pnl=0.6)
        res = eng.extend_position_hold_hours(db_session, pos.id, 4.0)
        assert res is not None
        assert res["after_max_hours"] == pytest.approx(16.0)
        # [§3.7] 放行一并落影子遥测（与 reject 成对统计）
        ev = db_session.query(PositionExitEvent).filter(
            PositionExitEvent.event_type == "postfill_telemetry_extend_granted",
        ).first()
        assert ev is not None

    def test_reject_when_funding_cost_over_20pct(self, db_session, make_mid_pos, monkeypatch):
        from types import SimpleNamespace as _SN
        eng = self._patch(monkeypatch)
        pos = make_mid_pos(
            8823, size=1.0, peak_unrealized_pnl=6.0, unrealized_pnl=0.5)

        class _FakeUDP:
            def __init__(self):
                pass

            def get_snapshot(self, max_age=0):
                return _SN(indicators={
                    "BTC": {"funding_rate": 0.01, "last_price": 100.0},
                })

        monkeypatch.setattr(
            "backend.services.unified_data_pool.UnifiedDataPool", _FakeUDP)
        # 预计成本 = 0.01×100×1×(8/8) = $1.0 ≥ 0.2×0.5=$0.1 → 拒绝
        res = eng.extend_position_hold_hours(db_session, pos.id, 8.0)
        assert res is None

    def test_gate_can_be_disabled(self, db_session, make_mid_pos, monkeypatch):
        monkeypatch.setenv("POSTFILL_EXTEND_HARD_GATE", "false")
        eng = self._patch(monkeypatch)
        pos = make_mid_pos(8824)  # peak=0 < 1R，但门已关
        res = eng.extend_position_hold_hours(db_session, pos.id, 4.0)
        assert res is not None


# ════════════════════════════════════════════════════════════════════
# 11. 硬线全平补登 ExitSource 事件（设计 §3.5 账实分离）
# ════════════════════════════════════════════════════════════════════
class TestHardLineExitSource:
    def test_sl_hit_records_stop_loss_source(self, db_session, monkeypatch):
        import json
        from backend.database.models import (
            PaperBalance, PaperPosition, PositionExitEvent,
        )
        db_session.add(PaperBalance(
            account_id=8831, available_balance=10000.0,
            total_equity=10000.0, realized_pnl=0.0,
        ))
        pos = PaperPosition(
            account_id=8831, symbol="BTC", side="long", size=0.1,
            entry_price=100.0, mark_price=100.0, leverage=5, margin=0.2,
            status="open", trade_nature="scalp", timeframe_tier="short",
            sl_price=95.0,
        )
        db_session.add(pos)
        db_session.commit()

        from backend.services.paper_trading_engine import paper_engine
        monkeypatch.setattr(
            paper_engine, "_resolve_account_exchange",
            lambda self, db, aid=None: "hyperliquid")
        monkeypatch.setattr(
            paper_engine, "_get_mark_price",
            lambda self, sym, ex=None: 95.0)
        monkeypatch.setattr(
            paper_engine, "_get_current_price",
            lambda self, sym, ex=None: 95.0)

        paper_engine.reprice_position(db_session, pos)

        events = db_session.query(PositionExitEvent).filter(
            PositionExitEvent.event_type == "hard_line_close").all()
        assert len(events) == 1
        meta = json.loads(events[0].metadata_json)
        assert meta["exit_source"] == "sl"


# ════════════════════════════════════════════════════════════════════
# 12. 影子遥测（§3.7：无 A/B 直接全开的补位机制）
# ════════════════════════════════════════════════════════════════════
class TestShadowTelemetry:
    def test_single_tier_counterfactual_event(self, db_session, monkeypatch):
        import json
        from backend.database.models import (
            PaperBalance, PaperPosition, PositionExitEvent,
        )
        db_session.add(PaperBalance(
            account_id=8891, available_balance=10000.0,
            total_equity=10000.0, realized_pnl=0.0,
        ))
        pos = PaperPosition(
            account_id=8891, symbol="BTC", side="long", size=0.20,
            entry_price=100.0, mark_price=100.0, leverage=5, margin=0.4,
            status="open", trade_nature="scalp", timeframe_tier="short",
            peak_pnl_pct=0.0, sl_price=0.0,
        )
        db_session.add(pos)
        db_session.commit()

        from backend.services.paper_trading_engine import paper_engine
        monkeypatch.setattr(
            paper_engine, "close_position",
            lambda db, aid, sym, side, reason="manual", **kw: (
                {"closed_fully": True, "pnl": 0}
            ),
        )
        done = paper_engine._run_unified_staged_tp(
            db_session, pos, 100.0, 102.5, 0.025,
            atr_pct=0.01,  # $20 名义 → 单档全平；Δ=2.5ATR ≥ tp1_mult 2.0
        )
        assert done is True

        ev = db_session.query(PositionExitEvent).filter(
            PositionExitEvent.event_type
            == "postfill_telemetry_staged_tp_cf",
        ).first()
        assert ev is not None
        meta = json.loads(ev.metadata_json)
        assert meta["mode"] == "single"
        assert meta["legacy_close_pct"] == 0.25
        assert meta["actual"] == "full_close"

    def test_observe_only_env_parse(self, monkeypatch):
        from backend.services.full_auto.scalp_position_review import (
            _observe_only_enabled,
        )
        monkeypatch.setenv("POSTFILL_OBSERVE_ONLY", "false")
        assert _observe_only_enabled() is False
        monkeypatch.setenv("POSTFILL_OBSERVE_ONLY", "true")
        assert _observe_only_enabled() is True
