"""
P1.6 alpha 容量模型测试。

完成标准（方案 P1.6）：
    - 活跃集每因子输出 capacity_usd
    - 容量正比于 ADV（流动性好的品种容量大）
    - 容量反比于冲击系数（薄盘容量小）
    - 容量随 Sharpe 衰减容忍增加
    - estimate_lambda 从 L2 合理估算
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.services.factor_engine.capacity import (
    CapacityModelConfig,
    compute_capacity,
    estimate_lambda_from_l2,
    portfolio_capacity,
)

pytestmark = pytest.mark.unit


class TestComputeCapacity:
    def test_returns_positive(self):
        cap = compute_capacity(
            adv_usd=1e8, expected_return_per_trade=0.002,
            base_sharpe=2.0,
        )
        assert cap > 0

    def test_scales_with_adv(self):
        """容量正比于 ADV（大品种容量大）。"""
        cap_small = compute_capacity(adv_usd=1e6, expected_return_per_trade=0.002, base_sharpe=2.0)
        cap_large = compute_capacity(adv_usd=1e9, expected_return_per_trade=0.002, base_sharpe=2.0)
        assert cap_large > cap_small * 100

    def test_inverse_with_lambda(self):
        """容量反比于冲击系数（薄盘 lambda 大容量小）。"""
        cap_low_lambda = compute_capacity(
            adv_usd=1e8, expected_return_per_trade=0.002, base_sharpe=2.0, lambda_bps=5.0,
        )
        cap_high_lambda = compute_capacity(
            adv_usd=1e8, expected_return_per_trade=0.002, base_sharpe=2.0, lambda_bps=50.0,
        )
        assert cap_low_lambda > cap_high_lambda

    def test_zero_return_zero_capacity(self):
        cap = compute_capacity(adv_usd=1e8, expected_return_per_trade=0.0, base_sharpe=2.0)
        assert cap == 0.0

    def test_zero_sharpe_zero_capacity(self):
        cap = compute_capacity(adv_usd=1e8, expected_return_per_trade=0.002, base_sharpe=0.0)
        assert cap == 0.0

    def test_safety_factor_reduces_capacity(self):
        cfg_default = CapacityModelConfig(safety_factor=1.0)
        cfg_conservative = CapacityModelConfig(safety_factor=0.25)
        cap_default = compute_capacity(1e8, 0.002, 2.0, config=cfg_default)
        cap_conservative = compute_capacity(1e8, 0.002, 2.0, config=cfg_conservative)
        assert cap_conservative < cap_default


class TestEstimateLambda:
    def test_thin_book_high_lambda(self):
        """薄盘（depth/ADV 小）→ 高 lambda。"""
        rng = np.random.default_rng(1)
        l2 = rng.uniform(1e3, 5e3, 100)  # 极薄
        adv = np.full(100, 1e8)
        lam = estimate_lambda_from_l2(l2, adv)
        assert lam > 5.0  # 薄盘冲击大

    def test_deep_book_low_lambda(self):
        """深盘（depth/ADV 大）→ 低 lambda。"""
        rng = np.random.default_rng(2)
        l2 = rng.uniform(5e6, 1e7, 100)  # 极深
        adv = np.full(100, 1e8)
        lam = estimate_lambda_from_l2(l2, adv)
        assert lam < 10.0  # 深盘冲击小

    def test_insufficient_data_returns_default(self):
        lam = estimate_lambda_from_l2(np.array([100.0]), np.array([1e8]))
        assert lam == CapacityModelConfig().lambda_bps  # 默认


class TestPortfolioCapacity:
    def test_aggregates(self):
        caps = [1e5, 2e5, 3e5]
        pc = portfolio_capacity(caps)
        assert pc > 0
        assert pc < sum(caps)  # 安全系数折减

    def test_handles_zeros(self):
        pc = portfolio_capacity([0.0, 1e5, 0.0])
        assert pc > 0

    def test_empty(self):
        assert portfolio_capacity([]) == 0.0
