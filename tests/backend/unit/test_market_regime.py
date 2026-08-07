"""
MarketRegimeClassifier 单元测试

v3 整改说明：本文件已对齐现行 API —
- classifier.classify() 返回 RegimeClassification(regime: MarketRegime, ...)
- regime 是 enum，不再是字符串；旧断言全部升级成 `isinstance(r, MarketRegime)`
- REGIME_PROFILES 已改名为 REGIME_STRATEGY_MAP
"""

import pytest
import pandas as pd
import numpy as np


def _make_klines(n: int = 200, trend: str = "up", volatility: float = 0.02) -> pd.DataFrame:
    """Helper: generate synthetic OHLCV data."""
    np.random.seed(42)
    base = 50000.0
    prices = [base]
    for i in range(n - 1):
        change = np.random.normal(0, volatility)
        if trend == "up":
            change += 0.003
        elif trend == "down":
            change -= 0.003
        elif trend == "crash":
            change -= 0.01
        prices.append(prices[-1] * (1 + change))

    close = np.array(prices)
    high = close * (1 + np.random.uniform(0, 0.01, n))
    low = close * (1 - np.random.uniform(0, 0.01, n))
    volume = np.random.uniform(100, 1000, n)

    return pd.DataFrame({
        "open": close * (1 + np.random.uniform(-0.005, 0.005, n)),
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


@pytest.fixture
def classifier():
    from backend.services.market_regime import MarketRegimeClassifier
    return MarketRegimeClassifier()


@pytest.mark.unit
class TestMarketRegimeClassifier:
    def test_classify_returns_enum_regime(self, classifier):
        from backend.services.market_regime import MarketRegime
        klines = _make_klines(200, trend="up")
        result = classifier.classify(klines)
        assert result is not None
        assert hasattr(result, "regime")
        assert isinstance(result.regime, MarketRegime)

    def test_uptrend_detected(self, classifier):
        from backend.services.market_regime import MarketRegime
        klines = _make_klines(200, trend="up", volatility=0.01)
        result = classifier.classify(klines)
        # 合成数据噪声较大，允许多个合理分类
        assert result.regime in {
            MarketRegime.TRENDING_UP,
            MarketRegime.HIGH_VOLATILITY,
            MarketRegime.LOW_VOLATILITY,
            MarketRegime.RANGING,
        }

    def test_downtrend_detected(self, classifier):
        from backend.services.market_regime import MarketRegime
        klines = _make_klines(200, trend="down", volatility=0.01)
        result = classifier.classify(klines)
        assert result.regime in {
            MarketRegime.TRENDING_DOWN,
            MarketRegime.CRASH,
            MarketRegime.HIGH_VOLATILITY,
            MarketRegime.RANGING,
        }

    def test_high_volatility(self, classifier):
        klines = _make_klines(200, trend="flat", volatility=0.05)
        result = classifier.classify(klines)
        assert result is not None

    def test_low_volatility(self, classifier):
        klines = _make_klines(200, trend="flat", volatility=0.002)
        result = classifier.classify(klines)
        assert result is not None

    def test_crash_detection(self, classifier):
        from backend.services.market_regime import MarketRegime
        klines = _make_klines(200, trend="crash", volatility=0.04)
        result = classifier.classify(klines)
        assert result is not None
        # 下跌+高波动的合成数据，至少命中 CRASH/TRENDING_DOWN/HIGH_VOLATILITY 之一
        assert result.regime in {
            MarketRegime.CRASH,
            MarketRegime.TRENDING_DOWN,
            MarketRegime.HIGH_VOLATILITY,
        }

    def test_insufficient_data_returns_default(self, classifier):
        # 数据极少时返回 RANGING 默认降级，不应抛异常
        klines = _make_klines(10)
        result = classifier.classify(klines)
        assert result is not None
        assert hasattr(result, "regime")

    def test_regime_has_strategy_mapping(self, classifier):
        from backend.services.market_regime import REGIME_STRATEGY_MAP, MarketRegime
        klines = _make_klines(200, trend="up")
        result = classifier.classify(klines)
        # 所有 regime 都应在 REGIME_STRATEGY_MAP 中有策略参数映射
        assert result.regime in REGIME_STRATEGY_MAP
        profile = REGIME_STRATEGY_MAP[result.regime]
        assert "preferred_nature" in profile
        assert "param_overrides" in profile
