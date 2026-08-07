"""
test_phase4_market_scan — Phase 4 市场扫描与异常检测单元测试

覆盖范围:
1. MarketScanner — 全市场扫描器
2. CandidatePool — 高价值交易对动态管理
3. AnomalyDetector — 异常检测引擎
4. HypothesisGenerator — LLM驱动假设生成器
5. 模块间集成测试
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
import pandas as pd
import numpy as np

# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_klines(rows: int = 120, base_price: float = 100.0,
                 vol_base: float = 1000.0, seed: int = 42) -> pd.DataFrame:
    """构造模拟K线数据"""
    np.random.seed(seed)
    closes = base_price + np.cumsum(np.random.randn(rows) * 0.5)
    volumes = vol_base + np.abs(np.random.randn(rows)) * vol_base * 0.5
    highs = closes + np.abs(np.random.randn(rows)) * 0.3
    lows = closes - np.abs(np.random.randn(rows)) * 0.3
    opens = closes + np.random.randn(rows) * 0.2

    df = pd.DataFrame({
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
    })
    return df


def _make_klines_with_oi(rows: int = 120, seed: int = 42) -> pd.DataFrame:
    """构造包含OI列的K线数据"""
    df = _make_klines(rows, seed=seed)
    df['oi'] = 1_000_000 + np.abs(np.random.randn(rows)) * 100_000
    return df


def _make_price_spike_klines(spike_idx: int = -1, lookback: int = 100) -> pd.DataFrame:
    """构造包含价格突刺的K线数据"""
    np.random.seed(99)
    rows = lookback + 10
    closes = 100.0 + np.cumsum(np.random.randn(rows) * 0.3)
    # 在指定位置制造突刺
    closes[spike_idx] = closes[spike_idx - 1] + 15.0  # 大幅偏离

    volumes = 1000.0 + np.abs(np.random.randn(rows)) * 500
    return pd.DataFrame({
        'open': closes,
        'high': closes + 1,
        'low': closes - 1,
        'close': closes,
        'volume': volumes,
    })


def _make_volume_spike_klines(spike_idx: int = -1, lookback: int = 100) -> pd.DataFrame:
    """构造包含成交量激增的K线数据"""
    np.random.seed(88)
    rows = lookback + 10
    closes = 100.0 + np.cumsum(np.random.randn(rows) * 0.3)
    volumes = 1000.0 + np.abs(np.random.randn(rows)) * 200

    # 制造成交量突刺
    volumes[spike_idx] = volumes[spike_idx - 1] * 10

    return pd.DataFrame({
        'open': closes,
        'high': closes + 1,
        'low': closes - 1,
        'close': closes,
        'volume': volumes,
    })


def _make_oi_divergence_klines() -> pd.DataFrame:
    """构造包含OI背离的K线数据（价格下跌+OI上升）"""
    rows = 30
    np.random.seed(77)
    # 价格下跌
    closes = 100.0 - np.linspace(0, 10, rows) + np.random.randn(rows) * 0.5
    # OI上升
    oi = 1_000_000 + np.linspace(0, 200_000, rows)

    return pd.DataFrame({
        'open': closes,
        'high': closes + 0.5,
        'low': closes - 0.5,
        'close': closes,
        'volume': np.full(rows, 1000.0),
        'oi': oi,
    })


# ════════════════════════════════════════════════════════
#  1. MarketScanner Tests
# ════════════════════════════════════════════════════════

class TestSymbolScore:
    """SymbolScore 数据类测试"""

    def test_creation_defaults(self):
        from backend.services.market_scanner import SymbolScore
        s = SymbolScore(
            symbol="BTC", total_score=75.0,
            volume_score=20.0, volatility_score=18.0,
            trend_score=22.0, funding_score=10.0, anomaly_score=5.0,
        )
        assert s.symbol == "BTC"
        assert s.total_score == 75.0
        assert s.reasons == []
        assert isinstance(s.timestamp, datetime)

    def test_with_reasons(self):
        from backend.services.market_scanner import SymbolScore
        s = SymbolScore(
            symbol="ETH", total_score=50.0,
            volume_score=10.0, volatility_score=15.0,
            trend_score=15.0, funding_score=5.0, anomaly_score=5.0,
            reasons=["high_volume", "strong_trend"],
        )
        assert len(s.reasons) == 2


class TestScanResult:
    """ScanResult 数据类测试"""

    def test_creation(self):
        from backend.services.market_scanner import ScanResult
        r = ScanResult(
            scan_id="scan_123",
            total_symbols_scanned=100,
            qualified_symbols=[],
            new_opportunities=["BTC", "ETH"],
            removed_symbols=["DOGE"],
        )
        assert r.scan_id == "scan_123"
        assert r.total_symbols_scanned == 100
        assert len(r.new_opportunities) == 2


class TestMarketScanner:
    """MarketScanner 核心逻辑测试"""

    def _make_mock_data_pool(self, klines_map=None, market_map=None):
        """构造模拟data_pool"""
        pool = MagicMock()
        klines_map = klines_map or {}
        market_map = market_map or {}

        def get_klines(symbol, tf):
            return klines_map.get(symbol)

        def get_market_data(symbol):
            return market_map.get(symbol, {})

        pool.get_klines = get_klines
        pool.get_market_data = get_market_data
        return pool

    def test_init_defaults(self):
        from backend.services.market_scanner import MarketScanner
        scanner = MarketScanner()
        assert scanner._current_pool == set()
        assert scanner._last_scan is None
        assert scanner.TOP_N == 20

    def test_should_rescan_no_previous_scan(self):
        from backend.services.market_scanner import MarketScanner
        scanner = MarketScanner()
        assert scanner.should_rescan() is True

    def test_should_rescan_recently_scanned(self):
        from backend.services.market_scanner import MarketScanner
        scanner = MarketScanner()
        scanner._last_scan = datetime.now()
        assert scanner.should_rescan() is False

    def test_should_rescan_after_interval(self):
        from backend.services.market_scanner import MarketScanner
        scanner = MarketScanner()
        scanner._last_scan = datetime.now() - timedelta(seconds=3601)
        assert scanner.should_rescan() is True

    def test_evaluate_symbol_high_volume(self):
        """高成交量交易对应获得高volume_score"""
        from backend.services.market_scanner import MarketScanner
        klines = _make_klines(120, base_price=100.0, vol_base=50_000.0)
        pool = self._make_mock_data_pool(
            klines_map={"BTC": klines},
            market_map={"BTC": {"funding_rate": 0.001}},
        )
        scanner = MarketScanner(data_pool=pool)

        score = asyncio.get_event_loop().run_until_complete(
            scanner._evaluate_symbol("BTC")
        )
        assert score.volume_score > 0
        assert score.total_score > 0

    def test_evaluate_symbol_insufficient_data(self):
        """数据不足时返回零分"""
        from backend.services.market_scanner import MarketScanner
        short_klines = _make_klines(10)
        pool = self._make_mock_data_pool(klines_map={"BTC": short_klines})
        scanner = MarketScanner(data_pool=pool)

        score = asyncio.get_event_loop().run_until_complete(
            scanner._evaluate_symbol("BTC")
        )
        assert score.total_score == 0

    def test_evaluate_symbol_no_data(self):
        """无数据时返回零分"""
        from backend.services.market_scanner import MarketScanner
        pool = self._make_mock_data_pool()
        scanner = MarketScanner(data_pool=pool)

        score = asyncio.get_event_loop().run_until_complete(
            scanner._evaluate_symbol("UNKNOWN")
        )
        assert score.total_score == 0

    def test_evaluate_symbol_with_funding_rate(self):
        """高资金费率应获得高funding_score"""
        from backend.services.market_scanner import MarketScanner
        klines = _make_klines(120, vol_base=50_000.0)
        pool = self._make_mock_data_pool(
            klines_map={"ETH": klines},
            market_map={"ETH": {"funding_rate": 0.015}},  # 非常高的费率
        )
        scanner = MarketScanner(data_pool=pool)

        score = asyncio.get_event_loop().run_until_complete(
            scanner._evaluate_symbol("ETH")
        )
        assert score.funding_score > 0

    def test_full_scan_basic(self):
        """基本全市场扫描"""
        from backend.services.market_scanner import MarketScanner
        klines = _make_klines(120, vol_base=50_000.0)

        pool = self._make_mock_data_pool(
            klines_map={"BTC": klines, "ETH": klines, "DOGE": _make_klines(10)},
            market_map={"BTC": {"funding_rate": 0.001}, "ETH": {"funding_rate": 0.002}},
        )
        scanner = MarketScanner(data_pool=pool)

        result = asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(["BTC", "ETH", "DOGE", "UNKNOWN"])
        )

        assert result.total_symbols_scanned == 4
        assert result.scan_id.startswith("scan_")
        assert isinstance(result.qualified_symbols, list)
        assert isinstance(result.new_opportunities, list)
        assert isinstance(result.removed_symbols, list)

    def test_full_scan_tracks_pool_changes(self):
        """扫描应正确追踪新增和移除的交易对"""
        from backend.services.market_scanner import MarketScanner
        klines = _make_klines(120, vol_base=50_000.0)
        pool = self._make_mock_data_pool(
            klines_map={"BTC": klines, "ETH": klines},
        )
        scanner = MarketScanner(data_pool=pool)

        # 第一次扫描
        r1 = asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(["BTC", "ETH"])
        )
        # BTC/ETH 应为新发现
        assert len(r1.new_opportunities) > 0

        # 第二次扫描 — 同样结果
        r2 = asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(["BTC", "ETH"])
        )
        # 不应有新机会
        assert len(r2.new_opportunities) == 0

    def test_full_scan_top_n_limit(self):
        """扫描结果不超过TOP_N限制"""
        from backend.services.market_scanner import MarketScanner
        klines = _make_klines(120, vol_base=50_000.0)
        symbols = {f"SYM{i}": klines for i in range(30)}
        pool = self._make_mock_data_pool(klines_map=symbols)
        scanner = MarketScanner(data_pool=pool)
        scanner.TOP_N = 5

        result = asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(list(symbols.keys()))
        )
        assert len(result.qualified_symbols) <= 5

    def test_full_scan_handles_errors(self):
        """扫描过程中个别symbol出错不应中断"""
        from backend.services.market_scanner import MarketScanner

        pool = MagicMock()
        def raise_for_bad(symbol):
            if symbol == "BAD":
                raise ValueError("test error")
            return _make_klines(120, vol_base=50_000.0)
        pool.get_klines = raise_for_bad
        pool.get_market_data = lambda s: {}

        scanner = MarketScanner(data_pool=pool)
        result = asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(["BAD", "GOOD"])
        )
        # BAD 出错被跳过, GOOD 正常处理
        assert result.total_symbols_scanned == 2

    def test_score_history_tracking(self):
        """扫描应记录历史得分"""
        from backend.services.market_scanner import MarketScanner
        klines = _make_klines(120, vol_base=50_000.0)
        pool = self._make_mock_data_pool(klines_map={"BTC": klines})
        scanner = MarketScanner(data_pool=pool)

        asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(["BTC"])
        )
        assert "BTC" in scanner._history
        assert len(scanner._history["BTC"]) == 1


# ════════════════════════════════════════════════════════
#  2. CandidatePool Tests
# ════════════════════════════════════════════════════════

class TestCandidatePool:
    """CandidatePool 动态管理测试"""

    def _make_score(self, symbol: str, score: float):
        from backend.services.market_scanner import SymbolScore
        return SymbolScore(
            symbol=symbol, total_score=score,
            volume_score=score/5, volatility_score=score/5,
            trend_score=score/5, funding_score=score/5, anomaly_score=score/5,
        )

    def test_should_add_high_score(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool()
        assert pool.should_add(self._make_score("BTC", 60.0)) is True

    def test_should_add_low_score(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool()
        assert pool.should_add(self._make_score("DOGE", 20.0)) is False

    def test_should_add_blacklisted(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool(blacklist={"SCAM"})
        assert pool.should_add(self._make_score("SCAM", 80.0)) is False

    def test_should_add_cooling_down(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool(
            cooling_down={"BTC": datetime.now() + timedelta(hours=12)}
        )
        assert pool.should_add(self._make_score("BTC", 60.0)) is False

    def test_should_add_after_cooldown_expires(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool(
            cooling_down={"BTC": datetime.now() - timedelta(hours=1)}
        )
        assert pool.should_add(self._make_score("BTC", 60.0)) is True

    def test_should_add_at_max_capacity_with_higher_score(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool(max_active=2)
        pool.active = {
            "ETH": self._make_score("ETH", 50.0),
            "SOL": self._make_score("SOL", 55.0),
        }
        # 新symbol得分高于最低的
        assert pool.should_add(self._make_score("BTC", 70.0)) is True

    def test_should_add_at_max_capacity_with_lower_score(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool(max_active=2)
        pool.active = {
            "ETH": self._make_score("ETH", 50.0),
            "SOL": self._make_score("SOL", 55.0),
        }
        assert pool.should_add(self._make_score("DOGE", 45.0)) is False

    def test_should_remove_low_score(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool()
        pool.active["BTC"] = self._make_score("BTC", 20.0)
        assert pool.should_remove("BTC") is True

    def test_should_remove_high_score(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool()
        pool.active["BTC"] = self._make_score("BTC", 60.0)
        assert pool.should_remove("BTC") is False

    def test_should_remove_not_active(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool()
        assert pool.should_remove("UNKNOWN") is False

    def test_update_adds_and_removes(self):
        from backend.services.market_scanner import CandidatePool, ScanResult, SymbolScore
        pool = CandidatePool()
        pool.active["OLD"] = self._make_score("OLD", 50.0)

        result = ScanResult(
            scan_id="scan_1",
            total_symbols_scanned=5,
            qualified_symbols=[self._make_score("NEW", 70.0)],
            new_opportunities=["NEW"],
            removed_symbols=["OLD"],
        )
        pool.update(result)

        assert "NEW" in pool.active
        assert "OLD" not in pool.active
        assert "OLD" in pool.cooling_down

    def test_clear_expired_cooldowns(self):
        from backend.services.market_scanner import CandidatePool
        pool = CandidatePool()
        pool.cooling_down = {
            "BTC": datetime.now() - timedelta(hours=1),
            "ETH": datetime.now() + timedelta(hours=1),
        }
        pool.clear_expired_cooldowns()
        assert "BTC" not in pool.cooling_down
        assert "ETH" in pool.cooling_down


# ════════════════════════════════════════════════════════
#  3. AnomalyDetector Tests
# ════════════════════════════════════════════════════════

class TestAnomalyType:
    """AnomalyType 枚举测试"""

    def test_all_types(self):
        from backend.services.anomaly_detector import AnomalyType
        assert AnomalyType.PRICE_SPIKE.value == "price_spike"
        assert AnomalyType.VOLUME_SURGE.value == "volume_surge"
        assert AnomalyType.FUNDING_EXTREME.value == "funding_extreme"
        assert AnomalyType.OI_DIVERGENCE.value == "oi_divergence"
        assert AnomalyType.FACTOR_ANOMALY.value == "factor_anomaly"
        assert AnomalyType.CORRELATION_BREAK.value == "corr_break"


class TestAnomalyEvent:
    """AnomalyEvent 测试"""

    def test_is_critical_high_severity(self):
        from backend.services.anomaly_detector import AnomalyEvent, AnomalyType
        event = AnomalyEvent(
            event_id="test", symbol="BTC",
            anomaly_type=AnomalyType.PRICE_SPIKE,
            severity=0.9, z_score=3.5,
            description="test", raw_value=100.0,
            expected_range=(90, 110),
        )
        assert event.is_critical is True

    def test_is_critical_low_severity(self):
        from backend.services.anomaly_detector import AnomalyEvent, AnomalyType
        event = AnomalyEvent(
            event_id="test", symbol="BTC",
            anomaly_type=AnomalyType.PRICE_SPIKE,
            severity=0.5, z_score=2.0,
            description="test", raw_value=100.0,
            expected_range=(90, 110),
        )
        assert event.is_critical is False


class TestAnomalyDetectorPrice:
    """AnomalyDetector 价格异常检测"""

    def test_normal_price_no_anomaly(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines(120)
        report = detector.detect("BTC", klines, {})
        price_events = [e for e in report.events if e.anomaly_type.value == "price_spike"]
        # 正常随机数据不应产生价格异常
        assert len(price_events) == 0

    def test_price_spike_detected(self):
        from backend.services.anomaly_detector import AnomalyDetector, AnomalyType
        detector = AnomalyDetector()
        klines = _make_price_spike_klines()
        report = detector.detect("BTC", klines, {})
        price_events = [e for e in report.events if e.anomaly_type == AnomalyType.PRICE_SPIKE]
        assert len(price_events) >= 1
        assert any(e.severity > 0 for e in price_events)

    def test_short_klines_no_crash(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines(20)  # 不足lookback
        report = detector.detect("BTC", klines, {})
        assert isinstance(report.events, list)

    def test_empty_klines(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        empty_df = pd.DataFrame()
        report = detector.detect("BTC", empty_df, {})
        assert len(report.events) == 0


class TestAnomalyDetectorVolume:
    """AnomalyDetector 成交量异常检测"""

    def test_normal_volume_no_anomaly(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines(120)
        report = detector.detect("BTC", klines, {})
        vol_events = [e for e in report.events if e.anomaly_type.value == "volume_surge"]
        assert len(vol_events) == 0

    def test_volume_spike_detected(self):
        from backend.services.anomaly_detector import AnomalyDetector, AnomalyType
        detector = AnomalyDetector()
        klines = _make_volume_spike_klines()
        report = detector.detect("BTC", klines, {})
        vol_events = [e for e in report.events if e.anomaly_type == AnomalyType.VOLUME_SURGE]
        assert len(vol_events) >= 1

    def test_no_volume_column(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        df = pd.DataFrame({'close': np.random.randn(120) + 100})
        report = detector.detect("BTC", df, {})
        vol_events = [e for e in report.events if e.anomaly_type.value == "volume_surge"]
        assert len(vol_events) == 0


class TestAnomalyDetectorFunding:
    """AnomalyDetector 资金费率异常检测"""

    def test_extreme_funding_detected(self):
        from backend.services.anomaly_detector import AnomalyDetector, AnomalyType
        detector = AnomalyDetector()
        klines = _make_klines(120)
        market = {"funding_rate": 0.02}  # 2% — 远超阈值
        report = detector.detect("BTC", klines, market)
        fund_events = [e for e in report.events if e.anomaly_type == AnomalyType.FUNDING_EXTREME]
        assert len(fund_events) >= 1
        assert fund_events[0].severity > 0

    def test_normal_funding_no_anomaly(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines(120)
        market = {"funding_rate": 0.0001}  # 正常费率
        report = detector.detect("BTC", klines, market)
        fund_events = [e for e in report.events if e.anomaly_type.value == "funding_extreme"]
        assert len(fund_events) == 0

    def test_negative_funding_detected(self):
        from backend.services.anomaly_detector import AnomalyDetector, AnomalyType
        detector = AnomalyDetector()
        klines = _make_klines(120)
        market = {"funding_rate": -0.015}  # 负极端
        report = detector.detect("BTC", klines, market)
        fund_events = [e for e in report.events if e.anomaly_type == AnomalyType.FUNDING_EXTREME]
        assert len(fund_events) >= 1

    def test_empty_market_data(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines(120)
        report = detector.detect("BTC", klines, {})
        fund_events = [e for e in report.events if e.anomaly_type.value == "funding_extreme"]
        assert len(fund_events) == 0


class TestAnomalyDetectorOI:
    """AnomalyDetector OI背离检测"""

    def test_oi_divergence_detected(self):
        from backend.services.anomaly_detector import AnomalyDetector, AnomalyType
        detector = AnomalyDetector()
        klines = _make_oi_divergence_klines()
        report = detector.detect("BTC", klines, {"funding_rate": 0})
        oi_events = [e for e in report.events if e.anomaly_type == AnomalyType.OI_DIVERGENCE]
        assert len(oi_events) >= 1

    def test_no_oi_column(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines(120)
        report = detector.detect("BTC", klines, {})
        oi_events = [e for e in report.events if e.anomaly_type.value == "oi_divergence"]
        assert len(oi_events) == 0


class TestAnomalyDetectorFactor:
    """AnomalyDetector 因子异常检测"""

    def test_factor_anomaly_detected(self):
        from backend.services.anomaly_detector import AnomalyDetector, AnomalyType
        detector = AnomalyDetector()
        klines = _make_klines(120)

        # 模拟有z_score属性的信号对象
        mock_signal = MagicMock()
        mock_signal.z_score = 3.5
        mock_signal.raw_value = 1.5

        factor_signals = {"test_factor": mock_signal}
        report = detector.detect("BTC", klines, {}, factor_signals)
        factor_events = [e for e in report.events if e.anomaly_type == AnomalyType.FACTOR_ANOMALY]
        assert len(factor_events) >= 1

    def test_normal_factor_no_anomaly(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines(120)

        mock_signal = MagicMock()
        mock_signal.z_score = 1.0  # 正常范围

        report = detector.detect("BTC", klines, {}, {"test_factor": mock_signal})
        factor_events = [e for e in report.events if e.anomaly_type.value == "factor_anomaly"]
        assert len(factor_events) == 0


class TestAnomalyReport:
    """AnomalyReport 推荐动作测试"""

    def test_critical_event_triggers_alert(self):
        from backend.services.anomaly_detector import AnomalyDetector, AnomalyEvent, AnomalyType
        detector = AnomalyDetector()
        klines = _make_price_spike_klines()
        market = {"funding_rate": 0.02}
        report = detector.detect("BTC", klines, market)
        # 如果有任何severity > 0.8的事件，action应为alert
        if any(e.is_critical for e in report.events):
            assert report.recommended_action == "alert"

    def test_empty_report_investigate(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        empty_df = pd.DataFrame({'close': [100] * 120, 'volume': [100] * 120})
        report = detector.detect("BTC", empty_df, {})
        assert report.recommended_action == "investigate"
        assert report.total_anomaly_score == 0

    def test_report_fields(self):
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines(120)
        report = detector.detect("ETH", klines, {"funding_rate": 0.0001})
        assert report.symbol == "ETH"
        assert isinstance(report.events, list)
        assert isinstance(report.total_anomaly_score, float)
        assert report.recommended_action in ("investigate", "alert", "trade_opportunity")


# ════════════════════════════════════════════════════════
#  4. HypothesisGenerator Tests
# ════════════════════════════════════════════════════════

class TestTradingHypothesis:
    """TradingHypothesis 数据类测试"""

    def test_creation_defaults(self):
        from backend.services.hypothesis_generator import TradingHypothesis
        h = TradingHypothesis(
            hypothesis_id="hyp_1",
            symbol="BTC",
            direction="long",
            timeframe="4h",
            entry_condition="test",
            expected_move_pct=5.0,
            confidence=0.7,
        )
        assert h.direction == "long"
        assert h.supporting_evidence == []
        assert h.source == "anomaly"

    def test_with_all_fields(self):
        from backend.services.hypothesis_generator import TradingHypothesis
        h = TradingHypothesis(
            hypothesis_id="hyp_2",
            symbol="ETH",
            direction="short",
            timeframe="1d",
            entry_condition="breakout",
            expected_move_pct=-3.0,
            confidence=0.8,
            supporting_evidence=["evidence1"],
            risk_factors=["risk1"],
            backtest_params={"sl": 0.02},
            source="llm_insight",
        )
        assert h.source == "llm_insight"
        assert len(h.risk_factors) == 1


class TestHypothesisGeneratorRuleBased:
    """HypothesisGenerator 基于规则的降级方案"""

    def _make_report(self, symbol="BTC", score=0.6, events=None):
        from backend.services.anomaly_detector import AnomalyReport
        return AnomalyReport(
            symbol=symbol,
            events=events or [],
            total_anomaly_score=score,
            recommended_action="trade_opportunity",
        )

    def _make_funding_event(self, rate=0.02):
        from backend.services.anomaly_detector import AnomalyEvent, AnomalyType
        return AnomalyEvent(
            event_id="test_fund",
            symbol="BTC",
            anomaly_type=AnomalyType.FUNDING_EXTREME,
            severity=0.7,
            z_score=2.0,
            description="funding extreme",
            raw_value=rate,
            expected_range=(-0.01, 0.01),
        )

    def _make_volume_event(self):
        from backend.services.anomaly_detector import AnomalyEvent, AnomalyType
        return AnomalyEvent(
            event_id="test_vol",
            symbol="BTC",
            anomaly_type=AnomalyType.VOLUME_SURGE,
            severity=0.6,
            z_score=3.0,
            description="volume surge",
            raw_value=50000.0,
            expected_range=(0, 20000),
        )

    def test_no_reports_returns_empty(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()
        result = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies([])
        )
        assert result == []

    def test_low_score_report_skipped(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()
        report = self._make_report(score=0.1, events=[self._make_volume_event()])
        result = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies([report])
        )
        assert len(result) == 0

    def test_funding_event_generates_hypothesis(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()
        report = self._make_report(
            score=0.7,
            events=[self._make_funding_event(rate=0.02)],
        )
        result = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies([report])
        )
        assert len(result) >= 1
        assert result[0].symbol == "BTC"
        assert result[0].source == "anomaly"
        assert result[0].direction == "short"  # 正费率 → 做空

    def test_negative_funding_generates_long(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()
        report = self._make_report(
            score=0.7,
            events=[self._make_funding_event(rate=-0.02)],
        )
        result = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies([report])
        )
        assert len(result) >= 1
        assert result[0].direction == "long"

    def test_multiple_reports(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()
        reports = [
            self._make_report("BTC", 0.7, [self._make_funding_event()]),
            self._make_report("ETH", 0.7, [self._make_volume_event()]),
        ]
        result = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies(reports)
        )
        assert len(result) >= 2

    def test_hypothesis_has_backtest_params(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()
        report = self._make_report(
            score=0.7,
            events=[self._make_funding_event()],
        )
        result = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies([report])
        )
        assert len(result) >= 1
        assert "stop_loss_pct" in result[0].backtest_params
        assert "take_profit_pct" in result[0].backtest_params
        assert "leverage" in result[0].backtest_params


class TestHypothesisGeneratorLLM:
    """HypothesisGenerator LLM集成测试"""

    def test_llm_parse_hypotheses(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()

        json_response = '''{
            "hypotheses": [{
                "symbol": "ETH",
                "direction": "long",
                "timeframe": "4h",
                "entry_condition": "Pullback to support",
                "expected_move_pct": 5.0,
                "confidence": 0.7,
                "supporting_evidence": ["Volume surge"],
                "risk_factors": ["Market risk"],
                "backtest_params": {"stop_loss_pct": [0.02, 0.05]}
            }]
        }'''

        result = gen._parse_hypotheses(json_response)
        assert len(result) == 1
        assert result[0].symbol == "ETH"
        assert result[0].direction == "long"
        assert result[0].source == "llm_insight"
        assert result[0].confidence == 0.7

    def test_llm_parse_invalid_json(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()
        result = gen._parse_hypotheses("not valid json")
        assert result == []

    def test_llm_parse_empty_hypotheses(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        gen = HypothesisGenerator()
        result = gen._parse_hypotheses('{"hypotheses": []}')
        assert result == []

    def test_llm_failure_fallback(self):
        """LLM调用失败应降级到规则方案"""
        from backend.services.hypothesis_generator import HypothesisGenerator
        from backend.services.anomaly_detector import AnomalyReport, AnomalyEvent, AnomalyType

        mock_llm = MagicMock()
        mock_llm.chat.completions.create = AsyncMock(side_effect=Exception("API error"))

        gen = HypothesisGenerator(llm_client=mock_llm)

        event = AnomalyEvent(
            event_id="test", symbol="BTC",
            anomaly_type=AnomalyType.FUNDING_EXTREME,
            severity=0.7, z_score=2.0,
            description="test", raw_value=0.02,
            expected_range=(-0.01, 0.01),
        )
        report = AnomalyReport(
            symbol="BTC", events=[event],
            total_anomaly_score=0.7,
            recommended_action="trade_opportunity",
        )

        result = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies([report])
        )
        # 应降级到rule-based
        assert len(result) >= 1
        assert result[0].source == "anomaly"


class TestHypothesisGeneratorPrompt:
    """HypothesisGenerator prompt构建测试"""

    def test_build_prompt_basic(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        from backend.services.anomaly_detector import AnomalyReport
        gen = HypothesisGenerator()

        report = AnomalyReport(
            symbol="BTC", events=[],
            total_anomaly_score=0.5,
            recommended_action="investigate",
        )
        prompt = gen._build_anomaly_prompt([report], None, None, None)
        assert "BTC" in prompt
        assert "0.50" in prompt

    def test_build_prompt_with_extras(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        from backend.services.anomaly_detector import AnomalyReport
        gen = HypothesisGenerator()

        report = AnomalyReport(
            symbol="BTC", events=[],
            total_anomaly_score=0.5,
            recommended_action="investigate",
        )
        prompt = gen._build_anomaly_prompt(
            [report],
            market_regime="trending",
            whale_signals=["large buy"],
            news_signals=["ETF approved"],
        )
        assert "trending" in prompt
        assert "large buy" in prompt
        assert "ETF approved" in prompt

    def test_build_prompt_limits_to_5_reports(self):
        from backend.services.hypothesis_generator import HypothesisGenerator
        from backend.services.anomaly_detector import AnomalyReport
        gen = HypothesisGenerator()

        reports = [
            AnomalyReport(
                symbol=f"SYM{i}", events=[],
                total_anomaly_score=0.5,
                recommended_action="investigate",
            )
            for i in range(10)
        ]
        prompt = gen._build_anomaly_prompt(reports, None, None, None)
        # 只应包含前5个
        assert "SYM0" in prompt
        assert "SYM4" in prompt
        assert "SYM5" not in prompt


# ════════════════════════════════════════════════════════
#  5. Integration Tests
# ════════════════════════════════════════════════════════

class TestPhase4Integration:
    """Phase 4 模块间集成测试"""

    def test_scanner_to_anomaly_pipeline(self):
        """MarketScanner → AnomalyDetector 集成"""
        from backend.services.market_scanner import MarketScanner
        from backend.services.anomaly_detector import AnomalyDetector

        klines = _make_price_spike_klines()
        pool = MagicMock()
        pool.get_klines = lambda s, tf: klines
        pool.get_market_data = lambda s: {"funding_rate": 0.015}

        scanner = MarketScanner(data_pool=pool)
        result = asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(["BTC"])
        )

        # 对扫描结果中的symbol执行异常检测
        detector = AnomalyDetector()
        if result.qualified_symbols:
            sym = result.qualified_symbols[0].symbol
            report = detector.detect(sym, klines, {"funding_rate": 0.015})
            assert isinstance(report.events, list)

    def test_anomaly_to_hypothesis_pipeline(self):
        """AnomalyDetector → HypothesisGenerator 集成"""
        from backend.services.anomaly_detector import AnomalyDetector
        from backend.services.hypothesis_generator import HypothesisGenerator

        klines = _make_price_spike_klines()
        detector = AnomalyDetector()
        report = detector.detect("BTC", klines, {"funding_rate": 0.02})

        gen = HypothesisGenerator()
        hypotheses = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies([report])
        )
        assert isinstance(hypotheses, list)

    def test_full_pipeline(self):
        """完整 Pipeline: Scanner → Anomaly → Hypothesis"""
        from backend.services.market_scanner import MarketScanner
        from backend.services.anomaly_detector import AnomalyDetector
        from backend.services.hypothesis_generator import HypothesisGenerator

        klines = _make_klines_with_oi(120)
        pool = MagicMock()
        pool.get_klines = lambda s, tf: klines
        pool.get_market_data = lambda s: {"funding_rate": 0.001}

        # Step 1: 扫描
        scanner = MarketScanner(data_pool=pool)
        scan_result = asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(["BTC", "ETH"])
        )

        # Step 2: 异常检测
        detector = AnomalyDetector()
        reports = []
        for score in scan_result.qualified_symbols:
            report = detector.detect(score.symbol, klines, {"funding_rate": 0.001})
            reports.append(report)

        # Step 3: 假设生成
        gen = HypothesisGenerator()
        hypotheses = asyncio.get_event_loop().run_until_complete(
            gen.generate_from_anomalies(reports)
        )

        # 整个管线不应报错
        assert isinstance(hypotheses, list)

    def test_candidate_pool_with_scan_result(self):
        """CandidatePool 与 ScanResult 的集成"""
        from backend.services.market_scanner import MarketScanner, CandidatePool, SymbolScore

        # 使用足够高的vol_base确保得分超过40（should_add阈值）
        klines = _make_klines(120, vol_base=500_000.0)
        pool_mock = MagicMock()
        pool_mock.get_klines = lambda s, tf: klines
        pool_mock.get_market_data = lambda s: {"funding_rate": 0.005}

        scanner = MarketScanner(data_pool=pool_mock)
        scan_result = asyncio.get_event_loop().run_until_complete(
            scanner.full_scan(["BTC", "ETH", "SOL"])
        )

        candidate_pool = CandidatePool()
        candidate_pool.update(scan_result)

        # 验证更新操作不报错，且active结构正确
        assert isinstance(candidate_pool.active, dict)
        for sym, score in candidate_pool.active.items():
            assert isinstance(score, SymbolScore)
            assert score.total_score >= 40

    def test_all_modules_importable(self):
        """验证所有Phase 4模块可以被正常导入"""
        from backend.services.market_scanner import (
            MarketScanner, SymbolScore, ScanResult, CandidatePool
        )
        from backend.services.anomaly_detector import (
            AnomalyDetector, AnomalyEvent, AnomalyReport, AnomalyType
        )
        from backend.services.hypothesis_generator import (
            HypothesisGenerator, TradingHypothesis
        )
        # 确保类都存在
        assert MarketScanner is not None
        assert AnomalyDetector is not None
        assert HypothesisGenerator is not None
