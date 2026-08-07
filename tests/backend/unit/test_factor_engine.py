"""
Unit tests for factor engine — validates factor registration, calculation, and loading.
"""
import pytest
import pandas as pd
import numpy as np


class TestFactorRegistry:
    """Tests for the factor registry system."""

    def test_registry_singleton(self):
        from backend.services.factor_engine.factor_registry import FactorRegistry
        r1 = FactorRegistry()
        r2 = FactorRegistry()
        assert r1 is r2

    def test_register_factor_decorator(self):
        # v3 整改: FactorRegistry 内部存储已从 `_registry` 改为 `_factors`
        from backend.services.factor_engine.factor_registry import registry
        initial_count = len(registry._factors)
        assert initial_count >= 0


class TestDerivativesFactors:
    """Tests for derivatives factor calculations."""

    @pytest.fixture()
    def sample_data(self):
        n = 100
        return pd.DataFrame({
            "close": np.random.uniform(80000, 90000, n),
            "high": np.random.uniform(85000, 91000, n),
            "low": np.random.uniform(79000, 85000, n),
            "volume": np.random.uniform(1000, 5000, n),
            "funding_rate": np.random.uniform(-0.001, 0.001, n),
            "oi": np.random.uniform(1e9, 2e9, n),
            "long_short_ratio": np.random.uniform(0.8, 1.2, n),
        })

    def test_funding_oi_divergence(self, sample_data):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import (
            FundingOIDivergenceFactor,
        )
        factor = FundingOIDivergenceFactor({})
        result = factor.calculate(sample_data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(sample_data)

    def test_long_short_ratio(self, sample_data):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import (
            LongShortRatioFactor,
        )
        factor = LongShortRatioFactor({})
        result = factor.calculate(sample_data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(sample_data)

    def test_liquidation_pressure(self, sample_data):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import (
            LiquidationHeatmapFactor,
        )
        factor = LiquidationHeatmapFactor({})
        result = factor.calculate(sample_data)
        assert isinstance(result, pd.Series)

    def test_options_structure_graceful_degradation(self):
        """No options columns → should return zeros."""
        from backend.services.factor_engine.factors.derivatives.options_structure_factors import (
            OptionsStructureFactor,
        )
        data = pd.DataFrame({"close": [100, 101, 102]})
        factor = OptionsStructureFactor({})
        result = factor.calculate(data)
        assert (result == 0.0).all()

    def test_open_interest_momentum_graceful_degradation(self):
        """No OI column → should return zeros."""
        from backend.services.factor_engine.factors.derivatives.options_structure_factors import (
            OpenInterestFactor,
        )
        data = pd.DataFrame({"close": [100, 101, 102]})
        factor = OpenInterestFactor({})
        result = factor.calculate(data)
        assert (result == 0.0).all()

    def test_open_interest_momentum_with_data(self, sample_data):
        from backend.services.factor_engine.factors.derivatives.options_structure_factors import (
            OpenInterestFactor,
        )
        factor = OpenInterestFactor({})
        result = factor.calculate(sample_data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(sample_data)


class TestFactorLoader:
    """Tests for the automatic factor loading mechanism."""

    def test_loader_discovers_factors(self):
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        count = loader.discover_and_load_all()
        assert count > 0, "Should discover at least one factor"

    def test_loader_factor_info(self):
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        loader.discover_and_load_all()
        info = loader.get_factor_info()
        assert isinstance(info, dict)
        for fid, finfo in info.items():
            assert "name" in finfo
            assert "category" in finfo
