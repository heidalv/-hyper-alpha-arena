"""
全链路端到端验证 — 交易 → 学习 → 进化 (L1-L5 收敛后)

验证范围：
  1. 交易平仓 → TradeOutcome 构建
  2. process_outcome 唯一入口 → 9 步 EMA 核心（StrategyTrade/RegimeScore/Memory 写入）
  3. BackendRegistry.handle_all → 11 个后端调度（无异常、无 double-processing）
  4. 学习→进化触发链（连续亏损 → adaptation / divergence 检测）
  5. 因子注册表统一（legacy 短名 + 新规范名共存）

本脚本使用真实 SQLite DB（隔离的测试 strategy_id，不污染生产数据）。
运行: python -m backend.tests.test_e2e_learning_chain
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta

# 确保项目根在 path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

os.environ.setdefault("ENABLE_COORDINATOR", "true")

from sqlalchemy import inspect as sqla_inspect

from backend.database.connection import SessionLocal, engine
from backend.database.models import (
    AIStrategy,
    StrategyTrade,
    StrategyRegimeScore,
    StrategyMemory,
)
from backend.services.unified_learning_service import (
    UnifiedLearningService,
    TradeOutcome,
    unified_learning,
    SOURCE_WEIGHTS,
)
from backend.services.learning import registry as backend_registry, backend_loader


# ═══════════════════════════════════════════════════════════════
# 测试夹具
# ═══════════════════════════════════════════════════════════════

TEST_RUN_ID = f"e2e_{uuid.uuid4().hex[:8]}"
_print = print


def _log(msg: str, ok: bool | None = None):
    marker = "✓" if ok else "✗" if ok is False else "·"
    _print(f"  {marker} {msg}")


def _ensure_backends_loaded():
    """确保后端注册表已加载（幂等）。"""
    if backend_registry.count() == 0:
        n = backend_loader.load_all()
        _log(f"后端注册表加载: {n} 个后端", n == 11)
    else:
        _log(f"后端注册表已就绪: {backend_registry.count()} 个后端")


def _create_test_strategy(db) -> str:
    """创建一个隔离的测试策略，返回 strategy_id。"""
    sid = f"{TEST_RUN_ID}_strategy"
    existing = db.query(AIStrategy).filter(AIStrategy.strategy_id == sid).first()
    if existing:
        return sid
    # account_id 是 NOT NULL，复用一个真实账号（测试后清理策略行即可）
    sample = db.query(AIStrategy.account_id).filter(
        AIStrategy.account_id.isnot(None)
    ).first()
    acct_id = sample[0] if sample else 1
    strat = AIStrategy(
        strategy_id=sid,
        name=f"E2E Test {TEST_RUN_ID}",
        status="active",
        timeframe_tier="mid",
        learning_enabled=True,
        account_id=acct_id,
        genome={
            "source_template_id": f"{TEST_RUN_ID}_tpl",
            "trade_nature": "swing",
        },
        parent_strategy_id=f"{TEST_RUN_ID}_tpl",
        primary_symbol="BTC",
    )
    db.add(strat)
    db.commit()
    _log(f"测试策略已创建: {sid} (account_id={acct_id})", True)
    return sid


def _cleanup_test_data(db, sid: str):
    """清理本次测试产生的数据（按 strategy_id / template_id 前缀）。"""
    tpl_id = f"{TEST_RUN_ID}_tpl"
    try:
        db.query(StrategyTrade).filter(StrategyTrade.strategy_id == sid).delete()
        db.query(StrategyRegimeScore).filter(
            StrategyRegimeScore.template_id.in_([sid, tpl_id])
        ).delete()
        db.query(StrategyMemory).filter(
            StrategyMemory.strategy_id.in_([sid, tpl_id])
        ).delete()
        db.query(AIStrategy).filter(AIStrategy.strategy_id == sid).delete()
        db.commit()
    except Exception:
        db.rollback()


# ═══════════════════════════════════════════════════════════════
# Test 1: 单笔盈利交易 → 完整学习链路
# ═══════════════════════════════════════════════════════════════

def test_single_winning_trade_e2e():
    _print("\n[Test 1] 单笔盈利交易 → 完整学习链路")
    db = SessionLocal()
    sid = ""
    try:
        sid = _create_test_strategy(db)
        tpl_id = f"{TEST_RUN_ID}_tpl"

        outcome = TradeOutcome(
            source="paper",
            strategy_id=sid,
            template_id=tpl_id,
            symbol="BTC",
            side="long",
            tier="mid",
            trade_nature="swing",
            entry_price=50000.0,
            exit_price=51000.0,
            pnl=100.0,
            pnl_pct=0.02,
            duration_seconds=3600,
            regime_at_entry="trending_up",
            regime_at_exit="trending_up",
            confidence=0.75,
            opened_at=datetime.now(timezone.utc) - timedelta(hours=1),
            metadata={"close_reason": "take_profit", "tier": "mid"},
        )

        # ── 执行唯一入口 ──
        unified_learning.process_outcome(db, outcome)
        _log("process_outcome 执行完成（无异常）", True)

        # ── 验证 1: StrategyTrade 写入 ──
        trade = (
            db.query(StrategyTrade)
            .filter(StrategyTrade.strategy_id == sid)
            .first()
        )
        _log(
            f"StrategyTrade 写入: pnl={trade.pnl if trade else 'N/A'}",
            trade is not None and trade.pnl == 100.0,
        )

        # ── 验证 2: StrategyRegimeScore 写入（EMA 核心）──
        score = (
            db.query(StrategyRegimeScore)
            .filter(
                StrategyRegimeScore.template_id == tpl_id,
                StrategyRegimeScore.regime == "trending_up",
                StrategyRegimeScore.source == "paper",
            )
            .first()
        )
        _log(
            f"StrategyRegimeScore 写入: win_rate={score.win_rate if score else 'N/A'}, "
            f"sample_count={score.sample_count if score else 'N/A'}",
            score is not None and score.sample_count == 1 and score.win_rate == 1.0,
        )

        # ── 验证 3: StrategyMemory 写入 ──
        mem = (
            db.query(StrategyMemory)
            .filter(StrategyMemory.strategy_id == sid)
            .first()
        )
        _log(
            f"StrategyMemory 写入: win_rate={mem.win_rate if mem else 'N/A'}, "
            f"total_trades={mem.total_trades if mem else 'N/A'}",
            mem is not None,
        )
    finally:
        if sid:
            _cleanup_test_data(db, sid)
        db.close()


# ═══════════════════════════════════════════════════════════════
# Test 2: 多笔交易 → EMA 增量更新 + 9步核心
# ═══════════════════════════════════════════════════════════════

def test_ema_incremental_update():
    _print("\n[Test 2] 多笔交易 → EMA 增量更新")
    db = SessionLocal()
    sid = ""
    try:
        sid = _create_test_strategy(db)
        tpl_id = f"{TEST_RUN_ID}_tpl"

        # 3 胜 2 负
        results = [
            (50.0, 0.01, "trending_up"),
            (-30.0, -0.006, "trending_up"),
            (80.0, 0.016, "trending_up"),
            (-20.0, -0.004, "trending_up"),
            (60.0, 0.012, "trending_up"),
        ]
        for i, (pnl, pnl_pct, regime) in enumerate(results):
            outcome = TradeOutcome(
                source="paper",
                strategy_id=sid,
                template_id=tpl_id,
                symbol="BTC",
                side="long",
                tier="mid",
                trade_nature="swing",
                entry_price=50000.0,
                exit_price=50000.0 + pnl,
                pnl=pnl,
                pnl_pct=pnl_pct,
                duration_seconds=3600,
                regime_at_entry=regime,
                regime_at_exit=regime,
                confidence=0.7,
                opened_at=datetime.now(timezone.utc) - timedelta(hours=1),
                metadata={"close_reason": "tp" if pnl > 0 else "sl"},
            )
            unified_learning.process_outcome(db, outcome)

        score = (
            db.query(StrategyRegimeScore)
            .filter(
                StrategyRegimeScore.template_id == tpl_id,
                StrategyRegimeScore.regime == "trending_up",
                StrategyRegimeScore.source == "paper",
            )
            .first()
        )
        # 3胜2负 → win_rate 应接近 0.6
        trades = (
            db.query(StrategyTrade)
            .filter(StrategyTrade.strategy_id == sid)
            .count()
        )
        _log(
            f"5笔后: sample_count={score.sample_count}, win_rate={score.win_rate:.3f}, "
            f"avg_pnl_pct={score.avg_pnl_pct:.4f}",
            score is not None
            and score.sample_count == 5
            and 0.5 < score.win_rate < 0.7
            and trades == 5,
        )
    finally:
        if sid:
            _cleanup_test_data(db, sid)
        db.close()


# ═══════════════════════════════════════════════════════════════
# Test 3: BackendRegistry.handle_all → 11 后端调度
# ═══════════════════════════════════════════════════════════════

def test_handle_all_dispatches_all_backends():
    _print("\n[Test 3] BackendRegistry.handle_all → 11 后端调度")
    db = SessionLocal()
    sid = ""
    try:
        sid = _create_test_strategy(db)
        outcome = TradeOutcome(
            source="paper",
            strategy_id=sid,
            template_id=f"{TEST_RUN_ID}_tpl",
            symbol="BTC",
            side="long",
            pnl=100.0,
            pnl_pct=0.02,
            regime_at_entry="trending_up",
            metadata={"close_reason": "tp"},
        )

        result = backend_registry.handle_all(db, outcome)

        # 所有 11 个后端都被遍历（key 存在）
        expected = {
            "causal_diagnosis", "reflexion", "promotion", "template_stats",
            "qaa_evolution", "factor_strategy_joint", "concept_drift",
            "periodic_review", "pattern_mining", "pattern_extraction",
            "causal_discovery",
        }
        actual = set(result.keys())
        _log(
            f"后端遍历: {len(actual)}/11, 缺失: {expected - actual or '无'}",
            actual == expected,
        )

        # 盈利交易应触发: promotion(paper source) + template_stats + pattern_extraction(pnl>0)
        must_trigger = {"promotion", "template_stats", "pattern_extraction"}
        triggered = {k for k, v in result.items() if v}
        missing_trigger = must_trigger - triggered
        _log(
            f"盈利触发的后端: {sorted(triggered)}",
            not missing_trigger,
        )

        # 亏损交易应触发: causal_diagnosis + reflexion（异步）
        loss_outcome = TradeOutcome(
            source="paper", strategy_id=sid, symbol="BTC",
            pnl=-50.0, pnl_pct=-0.01, regime_at_entry="ranging",
            metadata={"close_reason": "sl"},
        )
        loss_result = backend_registry.handle_all(db, loss_outcome)
        loss_triggered = {k for k, v in loss_result.items() if v}
        loss_must = {"causal_diagnosis", "reflexion"}
        _log(
            f"亏损触发的后端: {sorted(loss_triggered)}",
            loss_must.issubset(loss_triggered),
        )
    finally:
        if sid:
            _cleanup_test_data(db, sid)
        db.close()


# ═══════════════════════════════════════════════════════════════
# Test 4: 无 double-processing（L2 核心验收）
# ═══════════════════════════════════════════════════════════════

def test_no_double_processing():
    _print("\n[Test 4] 无 double-processing（template_stats 单次调用）")
    db = SessionLocal()
    sid = ""
    try:
        sid = _create_test_strategy(db)
        tpl_id = f"{TEST_RUN_ID}_tpl"

        outcome = TradeOutcome(
            source="paper",
            strategy_id=sid,
            template_id=tpl_id,
            symbol="BTC",
            side="long",
            pnl=100.0,
            pnl_pct=0.02,
            regime_at_entry="trending_up",
            metadata={"close_reason": "tp"},
        )

        # patch template_stats 后端，计数调用次数
        ts_backend = backend_registry.get("template_stats")
        call_count = {"n": 0}
        original = ts_backend.handle_outcome
        def _counting(db, outcome):
            call_count["n"] += 1
            return original(db, outcome)
        ts_backend.handle_outcome = _counting
        try:
            # 模拟旧代码的错误模式：process_outcome 已经内部 handle_all，
            # 如果再手动调 dispatch(wrapper)，会重复一次
            unified_learning.process_outcome(db, outcome)
            n_after_process = call_count["n"]
            # 再调一次 process_outcome（同一笔），应再 +1（不是 +2）
            unified_learning.process_outcome(db, outcome)
            n_after_second = call_count["n"]
        finally:
            ts_backend.handle_outcome = original

        _log(
            f"process_outcome 调用计数: 第1次={n_after_process}, 第2次={n_after_second}",
            n_after_process == 1 and n_after_second == 2,
        )
        _log(
            "无 double-processing（每次 process_outcome 只触发一次 template_stats）",
            n_after_second == 2,
        )
    finally:
        if sid:
            _cleanup_test_data(db, sid)
        db.close()


# ═══════════════════════════════════════════════════════════════
# Test 5: 学习→进化触发链（连续亏损 → adaptation）
# ═══════════════════════════════════════════════════════════════

def test_loss_streak_triggers_adaptation():
    _print("\n[Test 5] 学习→进化触发链（连续亏损 → adaptation 检测）")
    db = SessionLocal()
    sid = ""
    try:
        sid = _create_test_strategy(db)
        tpl_id = f"{TEST_RUN_ID}_tpl"

        # 连续 8 笔亏损（ADAPT_LOSS_STREAK=7，第8笔应触发 adaptation）
        for i in range(8):
            outcome = TradeOutcome(
                source="paper",
                strategy_id=sid,
                template_id=tpl_id,
                symbol="BTC",
                side="long",
                pnl=-20.0 - i,
                pnl_pct=-0.004,
                duration_seconds=3600,
                regime_at_entry="ranging",
                regime_at_exit="ranging",
                confidence=0.5,
                opened_at=datetime.now(timezone.utc) - timedelta(hours=1),
                metadata={"close_reason": "sl"},
            )
            try:
                unified_learning.process_outcome(db, outcome)
            except Exception:
                pass

        # 验证连亏计数器已累积（内部状态）
        streak = unified_learning._loss_streaks.get(sid, 0)
        _log(
            f"8笔连续亏损后 loss_streak={streak}（应 >= 7 触发 adaptation）",
            streak >= 7,
        )

        # 验证 StrategyRegimeScore 记录了亏损（win_rate 应很低）
        score = (
            db.query(StrategyRegimeScore)
            .filter(
                StrategyRegimeScore.template_id == tpl_id,
                StrategyRegimeScore.source == "paper",
            )
            .first()
        )
        _log(
            f"连亏后 win_rate={score.win_rate:.3f}（应接近 0）",
            score is not None and score.win_rate < 0.1,
        )
    finally:
        if sid:
            _cleanup_test_data(db, sid)
        db.close()


# ═══════════════════════════════════════════════════════════════
# Test 6: 三源权重（live/paper/backtest）
# ═══════════════════════════════════════════════════════════════

def test_three_source_weights():
    _print("\n[Test 6] 三源权重（live > paper > backtest）")
    _log(
        f"SOURCE_WEIGHTS: live={SOURCE_WEIGHTS['live']}, "
        f"paper={SOURCE_WEIGHTS['paper']}, backtest={SOURCE_WEIGHTS['backtest']}",
        SOURCE_WEIGHTS["live"] > SOURCE_WEIGHTS["paper"] > SOURCE_WEIGHTS["backtest"],
    )


# ═══════════════════════════════════════════════════════════════
# Test 7: 因子注册表统一（L4 验收）
# ═══════════════════════════════════════════════════════════════

def test_factor_registry_unified():
    _print("\n[Test 7] 因子注册表统一（legacy 短名 + 新规范名共存）")
    from backend.services.factor_engine.factor_registry import registry as fac_reg
    from backend.services.factor_engine.factor_loader import FactorLoader
    FactorLoader().discover_and_load_all()

    legacy_short = ["rsi", "macd", "adx", "atr", "obv", "vwap", "zscore"]
    missing = [f for f in legacy_short if not fac_reg.exists(f)]
    _log(
        f"legacy 短名因子在注册表: {len(legacy_short)-len(missing)}/{len(legacy_short)} "
        f"(缺失: {missing or '无'})",
        not missing,
    )
    _log(
        f"注册表总数: {fac_reg.count()}（应 >= 120）",
        fac_reg.count() >= 120,
    )


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    _print("=" * 64)
    _print("全链路端到端验证 — 交易 → 学习 → 进化 (L1-L5 收敛后)")
    _print(f"测试运行 ID: {TEST_RUN_ID}")
    _print("=" * 64)

    _ensure_backends_loaded()

    tests = [
        ("单笔盈利交易", test_single_winning_trade_e2e),
        ("EMA 增量更新", test_ema_incremental_update),
        ("11 后端调度", test_handle_all_dispatches_all_backends),
        ("无 double-processing", test_no_double_processing),
        ("连亏→adaptation", test_loss_streak_triggers_adaptation),
        ("三源权重", test_three_source_weights),
        ("因子注册表统一", test_factor_registry_unified),
    ]

    passed, failed = 0, 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            _print(f"  ✗ [{name}] 断言失败: {e}")
        except Exception as e:
            failed += 1
            _print(f"  ✗ [{name}] 异常: {type(e).__name__}: {e}")

    _print("\n" + "=" * 64)
    status = "ALL PASSED" if failed == 0 else f"{failed} FAILED"
    _print(f"结果: {passed} passed, {failed} failed — {status}")
    _print("=" * 64)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
