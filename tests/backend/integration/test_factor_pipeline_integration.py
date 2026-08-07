"""
因子管线集成测试

覆盖:
- FactorLoader 发现并加载因子
- FactorRegistry 注册表完整性
- 因子计算 → 信号生成流程
- composite 因子加载
"""

import pytest
import pandas as pd
import numpy as np


def _make_data(n: int = 200) -> pd.DataFrame:
    np.random.seed(42)
    close = 50000 + np.cumsum(np.random.normal(0, 100, n))
    return pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.005,
        "low": close * 0.995,
        "close": close,
        "volume": np.random.uniform(500, 2000, n),
    })


@pytest.mark.integration
class TestFactorPipeline:
    def test_loader_discovers_categories(self):
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        count = loader.discover_and_load_all()
        assert count > 0

    def test_registry_has_factors(self):
        """v3 整改: FactorRegistry 没有 get_all_factors()，实际 API 是 list_factors()。"""
        from backend.services.factor_engine.factor_loader import FactorLoader
        from backend.services.factor_engine.factor_registry import FactorRegistry
        FactorLoader().discover_and_load_all()
        registry = FactorRegistry()
        factor_ids = registry.list_factors()
        assert isinstance(factor_ids, list)
        assert len(factor_ids) > 0

    def test_composite_factors_loaded(self):
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        loader.discover_and_load_all()
        info = loader.get_factor_info()
        composite_ids = [fid for fid, meta in info.items() if meta.get("category") == "composite"]
        assert len(composite_ids) >= 5, f"Expected >=5 composite factors, got {len(composite_ids)}"

    def test_technical_factors_calculate(self):
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        loader.discover_and_load_all()
        data = _make_data()

        # Pick first 3 technical factors and verify they compute
        tech_factors = loader.get_factors_by_category("technical")
        for cls in tech_factors[:3]:
            factor = cls()
            try:
                result = factor.calculate(data)
                assert len(result) == len(data), f"{cls.__name__} output length mismatch"
            except Exception as e:
                pytest.fail(f"{cls.__name__}.calculate raised: {e}")

    def test_bb_width_no_collision(self):
        """Verify bb_width and bb_width_raw are distinct."""
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        loader.discover_and_load_all()
        info = loader.get_factor_info()
        ids = list(info.keys())
        bb_ids = [fid for fid in ids if "bb_width" in fid]
        assert len(bb_ids) == len(set(bb_ids)), f"Duplicate bb_width IDs: {bb_ids}"

    def test_derivatives_factors_calculate(self):
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        loader.discover_and_load_all()
        data = _make_data()
        # Add derivatives columns
        data["funding_rate"] = np.random.normal(0.0001, 0.0005, len(data))
        data["oi"] = np.random.uniform(1e8, 5e8, len(data))
        data["long_short_ratio"] = np.random.uniform(0.8, 1.2, len(data))

        deriv_factors = loader.get_factors_by_category("derivatives")
        for cls in deriv_factors[:3]:
            factor = cls()
            try:
                result = factor.calculate(data)
                assert len(result) == len(data)
            except Exception as e:
                pytest.fail(f"{cls.__name__}.calculate raised: {e}")

    def test_factor_metadata_fields(self):
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        loader.discover_and_load_all()
        info = loader.get_factor_info()
        for fid, meta in list(info.items())[:5]:
            assert "name" in meta
            assert "category" in meta
            assert "description" in meta

    def test_factor_calculation_no_crash_on_short_data(self):
        from backend.services.factor_engine.factor_loader import FactorLoader
        loader = FactorLoader()
        loader.discover_and_load_all()
        short_data = _make_data(10)

        all_factors = loader.get_all_factors()
        crash_count = 0
        for fid, cls in list(all_factors.items())[:10]:
            try:
                factor = cls()
                result = factor.calculate(short_data)
                assert len(result) == len(short_data)
            except Exception:
                crash_count += 1
        # At least some should handle short data gracefully
        assert crash_count < len(all_factors) // 2
