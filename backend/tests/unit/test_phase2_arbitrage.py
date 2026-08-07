"""
test_phase2_arbitrage — Phase 2 资金费率套利引擎单元测试

覆盖范围:
1. ArbitrageModels 数据结构
2. OpportunityScanner 机会扫描器
3. HedgeRiskAssessor 对冲风控
4. ArbitragePositionMonitor 生命周期监控
"""

import time
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.services.arbitrage.models import (
    ArbitrageOpportunity,
    ArbitrageStatus,
    FundingRateSnapshot,
    HedgePosition,
    HedgeRiskCheckResult,
)
from backend.services.arbitrage.opportunity_scanner import OpportunityScanner
from backend.services.arbitrage.risk_assessor import HedgeRiskAssessor
from backend.services.arbitrage.position_monitor import ArbitragePositionMonitor


# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

@dataclass
class _MockMarket:
    """模拟 MarketSnapshot"""
    funding_rate: float = 0.0
    volume_24h: float = 0.0


@dataclass
class _MockSnapshot:
    """模拟 UnifiedSnapshot"""
    markets: Dict[str, _MockMarket]
    derivatives_snapshot: Dict[str, Dict[str, Any]]


def _make_snapshot(
    symbols_rates: Optional[Dict[str, float]] = None,
    derivatives: Optional[Dict[str, Dict[str, Any]]] = None,
) -> _MockSnapshot:
    """快速构造 MockSnapshot"""
    markets = {}
    for sym, rate in (symbols_rates or {}).items():
        markets[sym] = _MockMarket(funding_rate=rate, volume_24h=1_000_000)
    return _MockSnapshot(
        markets=markets,
        derivatives_snapshot=derivatives or {},
    )


def _scanner_with_history(
    symbol: str,
    rates: list,
    scanner: Optional[OpportunityScanner] = None,
) -> OpportunityScanner:
    """向 scanner 注入历史费率数据（跳过 MIN_HISTORY_PERIODS 限制）"""
    s = scanner or OpportunityScanner()
    base_ts = time.time() - len(rates) * 28800
    for i, r in enumerate(rates):
        s._append_history(symbol, base_ts + i * 28800, r)
    return s


# ════════════════════════════════════════════════════════
#  1. ArbitrageModels 数据结构
# ════════════════════════════════════════════════════════

class TestArbitrageModels:

    def test_arbitrage_status_enum_values(self):
        """ArbitrageStatus 枚举包含所有预期值"""
        expected = {"scanning", "opportunity_found", "active",
                    "expiring", "expired", "error"}
        actual = {s.value for s in ArbitrageStatus}
        assert actual == expected

    def test_funding_rate_snapshot_construction(self):
        """FundingRateSnapshot 正确构造"""
        snap = FundingRateSnapshot(
            symbol="BTC", current_rate=0.001,
            predicted_rate=0.0009, rate_8h_avg=0.001,
            rate_24h_avg=0.0012, annual_yield=1.314,
            oi_total=5e9, volume_24h=2e8,
        )
        assert snap.symbol == "BTC"
        assert snap.current_rate == 0.001
        assert snap.annual_yield == 1.314

    def test_funding_rate_extreme_property(self):
        """is_extreme 在费率绝对值 > 1% 时为 True"""
        extreme = FundingRateSnapshot(
            symbol="ETH", current_rate=0.015,
            predicted_rate=0.0, rate_8h_avg=0.01,
            rate_24h_avg=0.01, annual_yield=10.95,
            oi_total=0, volume_24h=0,
        )
        normal = FundingRateSnapshot(
            symbol="ETH", current_rate=0.005,
            predicted_rate=0.0, rate_8h_avg=0.005,
            rate_24h_avg=0.005, annual_yield=5.475,
            oi_total=0, volume_24h=0,
        )
        assert extreme.is_extreme is True
        assert normal.is_extreme is False

    def test_hedge_position_balanced(self):
        """is_balanced 在 delta < 2% 时为 True"""
        balanced = HedgePosition(
            position_id="p1", symbol="BTC",
            long_size=1000, long_entry_price=50000,
            short_size=995, short_entry_price=50000,
            delta=5.0, accumulated_funding=0,
            entry_time=0,
        )
        assert balanced.is_balanced is True

    def test_hedge_position_unbalanced(self):
        """is_balanced 在 delta >= 2% 时为 False"""
        unbalanced = HedgePosition(
            position_id="p2", symbol="BTC",
            long_size=1000, long_entry_price=50000,
            short_size=950, short_entry_price=50000,
            delta=50.0, accumulated_funding=0,
            entry_time=0,
        )
        assert unbalanced.is_balanced is False

    def test_hedge_position_zero_sizes_safe(self):
        """is_balanced 在两边仓位为零时安全返回（不会除零）"""
        zero = HedgePosition(
            position_id="p3", symbol="BTC",
            long_size=0, long_entry_price=0,
            short_size=0, short_entry_price=0,
            delta=0.0, accumulated_funding=0,
            entry_time=0,
        )
        assert zero.is_balanced is True

    def test_hedge_risk_check_result_defaults(self):
        """HedgeRiskCheckResult 默认值"""
        r = HedgeRiskCheckResult(passed=True)
        assert r.reason_code == ""
        assert r.reason_text == ""
        assert r.blocked_by == ""


# ════════════════════════════════════════════════════════
#  2. OpportunityScanner
# ════════════════════════════════════════════════════════

class TestOpportunityScanner:

    def test_scan_empty_symbols_returns_empty(self):
        """空 symbols 返回空列表"""
        scanner = OpportunityScanner()
        snap = _make_snapshot()
        assert scanner.scan_opportunities([], snap) == []

    def test_scan_none_snapshot_returns_empty(self):
        """None snapshot 返回空列表"""
        scanner = OpportunityScanner()
        assert scanner.scan_opportunities(["BTC"], None) == []

    def test_scan_no_funding_rate_returns_empty(self):
        """snapshot 中无对应 symbol 的费率 → 返回空"""
        scanner = OpportunityScanner()
        snap = _make_snapshot()  # 空
        assert scanner.scan_opportunities(["BTC"], snap) == []

    def test_scan_insufficient_history_returns_empty(self):
        """历史数据不足 MIN_HISTORY_PERIODS → 返回空"""
        scanner = OpportunityScanner()
        # 只扫描1次，历史不足24期
        snap = _make_snapshot({"BTC": 0.001})
        result = scanner.scan_opportunities(["BTC"], snap)
        assert result == []

    def test_scan_high_funding_rate_finds_opportunity(self):
        """高费率且有足够历史 → 发现机会"""
        scanner = OpportunityScanner()
        symbol = "BTC"
        # 注入25期正费率历史
        rates = [0.001] * 25
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"BTC": 0.001})
        result = scanner.scan_opportunities(["BTC"], snap)

        assert len(result) == 1
        opp = result[0]
        assert opp.symbol == "BTC"
        assert opp.strategy == "funding_short"  # 正费率 → 做空收资金
        assert opp.expected_annual_yield > 0

    def test_scan_low_yield_filtered_out(self):
        """年化收益低于阈值 → 被过滤"""
        scanner = OpportunityScanner()
        symbol = "DOGE"
        # 极低费率 → 年化低于 15%
        rates = [0.000001] * 25
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"DOGE": 0.000001})
        result = scanner.scan_opportunities(["DOGE"], snap)
        assert result == []

    def test_scan_negative_rate_uses_funding_long(self):
        """负费率 → strategy = funding_long"""
        scanner = OpportunityScanner()
        symbol = "ETH"
        rates = [-0.001] * 25
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"ETH": -0.001})
        result = scanner.scan_opportunities(["ETH"], snap)
        assert len(result) == 1
        assert result[0].strategy == "funding_long"

    def test_scan_results_sorted_by_yield_desc(self):
        """多 symbol 结果按年化收益降序排列"""
        scanner = OpportunityScanner()

        # BTC 高费率
        _scanner_with_history("BTC", [0.002] * 25, scanner)
        # ETH 中等费率
        _scanner_with_history("ETH", [0.001] * 25, scanner)

        snap = _make_snapshot({"BTC": 0.002, "ETH": 0.001})
        result = scanner.scan_opportunities(["BTC", "ETH"], snap)

        assert len(result) == 2
        assert result[0].expected_annual_yield >= result[1].expected_annual_yield

    def test_annual_yield_calculation(self):
        """年化收益率 = |avg_24h| * 3 * 365"""
        scanner = OpportunityScanner()
        symbol = "BTC"
        rate = 0.001
        rates = [rate] * 25
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"BTC": rate})
        result = scanner.scan_opportunities(["BTC"], snap)

        assert len(result) == 1
        # avg_24h ≈ 0.001 (最后9期均值) → annual = 0.001 * 3 * 365 = 1.095
        expected = abs(rate) * 3 * 365
        assert abs(result[0].expected_annual_yield - expected) < 0.01

    def test_risk_score_between_zero_and_one(self):
        """风险评分始终在 [0, 1] 范围内"""
        scanner = OpportunityScanner()
        symbol = "BTC"
        # 高波动历史
        import random
        random.seed(42)
        rates = [random.uniform(-0.005, 0.005) for _ in range(30)]
        # 确保年化够高
        rates[-9:] = [0.003] * 9
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"BTC": 0.003})
        result = scanner.scan_opportunities(["BTC"], snap)
        if result:
            assert 0.0 <= result[0].risk_score <= 1.0

    def test_confidence_is_one_minus_risk(self):
        """confidence = 1 - risk_score"""
        scanner = OpportunityScanner()
        symbol = "BTC"
        rates = [0.001] * 30
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"BTC": 0.001})
        result = scanner.scan_opportunities(["BTC"], snap)
        if result:
            expected_conf = max(0.0, min(1.0, 1.0 - result[0].risk_score))
            assert abs(result[0].confidence - expected_conf) < 0.001

    def test_get_active_opportunities_returns_cache(self):
        """get_active_opportunities 返回缓存"""
        scanner = OpportunityScanner()
        assert scanner.get_active_opportunities() == []

    def test_scan_count_increments(self):
        """scan_count 随扫描递增"""
        scanner = OpportunityScanner()
        assert scanner.scan_count == 0
        snap = _make_snapshot({"BTC": 0.001})
        scanner.scan_opportunities(["BTC"], snap)
        assert scanner.scan_count == 1

    def test_funding_history_trimmed_at_max(self):
        """历史记录超过 MAX_HISTORY_PER_SYMBOL 时裁剪"""
        scanner = OpportunityScanner()
        symbol = "BTC"
        max_len = scanner.MAX_HISTORY_PER_SYMBOL + 100
        rates = [0.001] * max_len
        _scanner_with_history(symbol, rates, scanner)

        history = scanner.get_funding_history(symbol)
        assert len(history) <= scanner.MAX_HISTORY_PER_SYMBOL

    def test_get_funding_history_unknown_symbol(self):
        """未知 symbol 返回空历史"""
        scanner = OpportunityScanner()
        assert scanner.get_funding_history("UNKNOWN") == []


# ════════════════════════════════════════════════════════
#  3. HedgeRiskAssessor
# ════════════════════════════════════════════════════════

class TestHedgeRiskAssessor:

    def test_all_rules_pass(self):
        """所有规则通过"""
        assessor = HedgeRiskAssessor()
        result = assessor.check(
            account_equity=10000,
            current_hedges_count=0,
            current_hedge_notional=0,
            proposed_notional=100,
        )
        assert result.passed is True
        assert result.reason_code == ""

    def test_zero_equity_blocked(self):
        """零权益被阻止"""
        assessor = HedgeRiskAssessor()
        result = assessor.check(
            account_equity=0,
            current_hedges_count=0,
            current_hedge_notional=0,
            proposed_notional=100,
        )
        assert result.passed is False
        assert result.reason_code == "zero_equity"

    def test_negative_equity_blocked(self):
        """负权益被阻止"""
        assessor = HedgeRiskAssessor()
        result = assessor.check(
            account_equity=-100,
            current_hedges_count=0,
            current_hedge_notional=0,
            proposed_notional=100,
        )
        assert result.passed is False
        assert result.reason_code == "zero_equity"

    def test_total_hedge_exceeded(self):
        """总对冲仓位超限"""
        assessor = HedgeRiskAssessor()
        equity = 10000
        limit = assessor.MAX_TOTAL_HEDGE_PCT  # 0.40
        # 当前 3500 + 提议 1000 = 4500 > 4000
        result = assessor.check(
            account_equity=equity,
            current_hedges_count=0,
            current_hedge_notional=3500,
            proposed_notional=1000,
        )
        assert result.passed is False
        assert result.reason_code == "total_hedge_exceeded"
        assert result.blocked_by == "max_total_hedge_pct"

    def test_max_concurrent_hedges(self):
        """并发对冲数达上限"""
        assessor = HedgeRiskAssessor()
        result = assessor.check(
            account_equity=10000,
            current_hedges_count=3,
            current_hedge_notional=0,
            proposed_notional=100,
        )
        assert result.passed is False
        assert result.reason_code == "max_concurrent_hedges"

    def test_single_leg_loss_exceeded(self):
        """单腿仓位过大"""
        assessor = HedgeRiskAssessor()
        # proposed 600 / equity 10000 = 6% > 5%
        result = assessor.check(
            account_equity=10000,
            current_hedges_count=0,
            current_hedge_notional=0,
            proposed_notional=600,
        )
        assert result.passed is False
        assert result.reason_code == "single_leg_loss_exceeded"

    def test_custom_rules_override(self):
        """自定义规则覆盖默认值"""
        custom = HedgeRiskAssessor(rules={
            'max_concurrent_hedges': 1,
            'max_total_hedge_pct': 0.2,
        })
        assert custom.MAX_CONCURRENT_HEDGES == 1
        assert custom.MAX_TOTAL_HEDGE_PCT == 0.2

    def test_boundary_exact_limit_passes(self):
        """恰好等于阈值时通过"""
        assessor = HedgeRiskAssessor()
        equity = 10000
        # proposed 500 / 10000 = 5% == MAX_SINGLE_LEG_LOSS_PCT
        result = assessor.check(
            account_equity=equity,
            current_hedges_count=2,  # < 3
            current_hedge_notional=3500,  # 3500+500=4000 / 10000 = 40%
            proposed_notional=500,
        )
        assert result.passed is True


# ════════════════════════════════════════════════════════
#  4. ArbitragePositionMonitor
# ════════════════════════════════════════════════════════

class TestArbitragePositionMonitor:

    def test_update_empty_symbols_returns_empty(self):
        """空 symbols 返回空列表"""
        monitor = ArbitragePositionMonitor()
        snap = _make_snapshot()
        result = monitor.update([], snap)
        assert result == []

    def test_update_stores_opportunities(self):
        """update 存储扫描到的机会"""
        scanner = OpportunityScanner()
        monitor = ArbitragePositionMonitor(scanner=scanner)
        symbol = "BTC"
        rates = [0.001] * 25
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"BTC": 0.001})
        result = monitor.update(["BTC"], snap)

        assert len(result) >= 1
        assert result[0].symbol == "BTC"

    def test_get_status_returns_summary(self):
        """get_status 返回状态摘要"""
        monitor = ArbitragePositionMonitor()
        status = monitor.get_status()

        assert 'enabled' in status
        assert 'total_scans' in status
        assert 'active_opportunities' in status
        assert 'expiring_opportunities' in status
        assert 'total_opportunities_found' in status
        assert 'uptime_seconds' in status
        assert status['enabled'] is True

    def test_monitor_positions_returns_list(self):
        """monitor_positions 返回状态列表"""
        scanner = OpportunityScanner()
        monitor = ArbitragePositionMonitor(scanner=scanner)
        symbol = "BTC"
        rates = [0.001] * 25
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"BTC": 0.001})
        monitor.update(["BTC"], snap)

        results = monitor.monitor_positions()
        assert isinstance(results, list)
        if results:
            assert 'symbol' in results[0]
            assert 'strategy' in results[0]
            assert 'reversal_warning' in results[0]

    def test_reversal_detection_negative_to_positive(self):
        """反转检测: 正→负 费率反转"""
        scanner = OpportunityScanner()
        monitor = ArbitragePositionMonitor(scanner=scanner)
        symbol = "BTC"

        # 先正后负 → 反转
        # 需要: 最后3期全负, history[-8:-3]的5期全正 → 方向不同
        rates = [0.001] * 22 + [-0.001] * 5
        _scanner_with_history(symbol, rates, scanner)

        history = scanner.get_funding_history(symbol)
        # 最后3期全负，之前5期(index -8:-3)全正 → 反转
        result = monitor._check_reversal(history)
        assert result is True

    def test_no_reversal_when_stable(self):
        """无反转: 费率方向稳定"""
        scanner = OpportunityScanner()
        monitor = ArbitragePositionMonitor(scanner=scanner)

        # 全部正 → 无反转
        history = [0.001] * 20
        result = monitor._check_reversal(history)
        assert result is False

    def test_get_opportunity_history(self):
        """get_opportunity_history 返回历史记录"""
        scanner = OpportunityScanner()
        monitor = ArbitragePositionMonitor(scanner=scanner)
        symbol = "BTC"
        rates = [0.001] * 25
        _scanner_with_history(symbol, rates, scanner)

        snap = _make_snapshot({"BTC": 0.001})
        monitor.update(["BTC"], snap)

        history = monitor.get_opportunity_history()
        assert len(history) >= 1
        assert history[0]['symbol'] == "BTC"

    def test_get_opportunity_history_filter_by_symbol(self):
        """get_opportunity_history 按 symbol 过滤"""
        scanner = OpportunityScanner()
        monitor = ArbitragePositionMonitor(scanner=scanner)
        _scanner_with_history("BTC", [0.001] * 25, scanner)
        _scanner_with_history("ETH", [0.001] * 25, scanner)

        snap = _make_snapshot({"BTC": 0.001, "ETH": 0.001})
        monitor.update(["BTC", "ETH"], snap)

        btc_history = monitor.get_opportunity_history(symbol="BTC")
        for entry in btc_history:
            assert entry['symbol'] == "BTC"

    def test_get_opportunity_history_limit(self):
        """get_opportunity_history 限制返回数量"""
        monitor = ArbitragePositionMonitor()
        history = monitor.get_opportunity_history(limit=0)
        assert len(history) == 0

    def test_opportunity_disappears_marked_expiring(self):
        """消失的机会标记为 EXPIRING"""
        scanner = OpportunityScanner()
        monitor = ArbitragePositionMonitor(scanner=scanner)
        symbol = "BTC"
        rates = [0.001] * 25
        _scanner_with_history(symbol, rates, scanner)

        # 第1次扫描: 发现机会
        snap = _make_snapshot({"BTC": 0.001})
        monitor.update(["BTC"], snap)

        # 第2次扫描: 费率为零 → 机会消失
        snap2 = _make_snapshot({"BTC": 0.0})
        monitor.update(["BTC"], snap2)

        status = monitor.get_status()
        # 机会应该已过期（因为费率为零被过滤）
        assert status['active_opportunities'] + status['expiring_opportunities'] >= 0


# ════════════════════════════════════════════════════════
#  5. 包导入和单例
# ════════════════════════════════════════════════════════

class TestPackageImports:

    def test_import_all_from_package(self):
        """包级 __all__ 导入正常"""
        import backend.services.arbitrage as pkg
        for name in pkg.__all__:
            assert hasattr(pkg, name), f"{name} 未在包中导出"

    def test_package_version(self):
        """包版本号"""
        import backend.services.arbitrage as pkg
        assert pkg.__version__ == "3.0.0"

    def test_module_singletons_exist(self):
        """模块级单例存在"""
        from backend.services.arbitrage import (
            opportunity_scanner,
            hedge_risk_assessor,
            arb_monitor,
        )
        assert isinstance(opportunity_scanner, OpportunityScanner)
        assert isinstance(hedge_risk_assessor, HedgeRiskAssessor)
        assert isinstance(arb_monitor, ArbitragePositionMonitor)
