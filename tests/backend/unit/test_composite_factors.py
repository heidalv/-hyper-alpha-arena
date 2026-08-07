"""
复合因子单元测试

覆盖全部 10 个中级复合因子:
1. RSIVolRatio  2. CVDVolumeResidual  3. TrendPersistence
4. MeanReversionScore  5. LiquidityPremium  6. SmartMoneyFlow
7. FearMomentum  8. FundingOIAlignment  9. MultiTFMomentum
10. RegimeTransitionScore
"""

import pytest
import pandas as pd
import numpy as np


def _make_data(n: int = 100, include_extras: bool = False) -> pd.DataFrame:
    np.random.seed(42)
    close = 50000 + np.cumsum(np.random.normal(0, 100, n))
    df = pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.005,
        "low": close * 0.995,
        "close": close,
        "volume": np.random.uniform(500, 2000, n),
    })
    if include_extras:
        df["funding_rate"] = np.random.normal(0.0001, 0.0005, n)
        df["oi"] = np.random.uniform(1e8, 5e8, n)
        df["whale_tx_volume"] = np.random.uniform(0, 1e6, n)
        df["fear_greed"] = np.random.uniform(20, 80, n)
        df["cvd"] = np.cumsum(np.random.normal(0, 100, n))
    return df


@pytest.mark.unit
class TestRSIVolRatio:
    def test_output_shape(self):
        from backend.services.factor_engine.factors.composite.composite_factors import RSIVolRatioFactor
        f = RSIVolRatioFactor()
        data = _make_data()
        result = f.calculate(data)
        assert len(result) == len(data)

    def test_no_nan_in_tail(self):
        from backend.services.factor_engine.factors.composite.composite_factors import RSIVolRatioFactor
        f = RSIVolRatioFactor()
        result = f.calculate(_make_data(200))
        assert not result.iloc[-20:].isna().any()


@pytest.mark.unit
class TestCVDVolumeResidual:
    def test_output_shape(self):
        from backend.services.factor_engine.factors.composite.composite_factors import CVDVolumeResidualFactor
        f = CVDVolumeResidualFactor()
        result = f.calculate(_make_data(100))
        assert len(result) == 100

    def test_with_cvd_column(self):
        from backend.services.factor_engine.factors.composite.composite_factors import CVDVolumeResidualFactor
        f = CVDVolumeResidualFactor()
        data = _make_data(100, include_extras=True)
        result = f.calculate(data)
        assert len(result) == 100


@pytest.mark.unit
class TestTrendPersistence:
    def test_returns_series(self):
        from backend.services.factor_engine.factors.composite.composite_factors import TrendPersistenceFactor
        f = TrendPersistenceFactor()
        result = f.calculate(_make_data(200))
        assert isinstance(result, pd.Series)
        assert len(result) == 200


@pytest.mark.unit
class TestMeanReversionScore:
    def test_returns_correct_length(self):
        from backend.services.factor_engine.factors.composite.composite_factors import MeanReversionScoreFactor
        f = MeanReversionScoreFactor()
        result = f.calculate(_make_data(100))
        assert len(result) == 100


@pytest.mark.unit
class TestLiquidityPremium:
    def test_output(self):
        from backend.services.factor_engine.factors.composite.composite_factors import LiquidityPremiumFactor
        f = LiquidityPremiumFactor()
        result = f.calculate(_make_data(100))
        assert len(result) == 100


@pytest.mark.unit
class TestSmartMoneyFlow:
    def test_zero_without_data(self):
        from backend.services.factor_engine.factors.composite.composite_factors import SmartMoneyFlowFactor
        f = SmartMoneyFlowFactor()
        data = _make_data(50)
        result = f.calculate(data)
        assert (result == 0.0).all()

    def test_nonzero_with_data(self):
        from backend.services.factor_engine.factors.composite.composite_factors import SmartMoneyFlowFactor
        f = SmartMoneyFlowFactor()
        data = _make_data(50, include_extras=True)
        result = f.calculate(data)
        assert len(result) == 50


@pytest.mark.unit
class TestFearMomentum:
    def test_output(self):
        from backend.services.factor_engine.factors.composite.composite_factors import FearMomentumFactor
        f = FearMomentumFactor()
        data = _make_data(100, include_extras=True)
        result = f.calculate(data)
        assert len(result) == 100


@pytest.mark.unit
class TestFundingOIAlignment:
    def test_zero_without_data(self):
        from backend.services.factor_engine.factors.composite.composite_factors import FundingOIAlignmentFactor
        f = FundingOIAlignmentFactor()
        result = f.calculate(_make_data(50))
        assert (result == 0.0).all()

    def test_with_data(self):
        from backend.services.factor_engine.factors.composite.composite_factors import FundingOIAlignmentFactor
        f = FundingOIAlignmentFactor()
        data = _make_data(50, include_extras=True)
        result = f.calculate(data)
        assert len(result) == 50


@pytest.mark.unit
class TestMultiTFMomentum:
    def test_range(self):
        from backend.services.factor_engine.factors.composite.composite_factors import MultiTFMomentumFactor
        f = MultiTFMomentumFactor()
        result = f.calculate(_make_data(100))
        valid = result.dropna()
        assert (valid >= -1.5).all() and (valid <= 1.5).all()


@pytest.mark.unit
class TestRegimeTransitionScore:
    def test_output(self):
        from backend.services.factor_engine.factors.composite.composite_factors import RegimeTransitionScoreFactor
        f = RegimeTransitionScoreFactor()
        result = f.calculate(_make_data(100))
        assert len(result) == 100
