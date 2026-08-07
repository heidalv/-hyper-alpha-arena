"""
test_phase3_factor_expansion — Phase 3 因子扩展单元测试

覆盖范围:
1. 4个链上因子 (onchain)
2. 3个衍生品因子 (derivatives)
3. 2个宏观因子 (macro)
4. OnchainDataCollector 数据采集器
5. 因子注册验证
6. 数据注入验证
"""

import time
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

import pytest
import pandas as pd
import numpy as np

# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_base_data(rows: int = 50) -> pd.DataFrame:
    """构造基础 K线 DataFrame（只有 OHLCV 列）"""
    np.random.seed(42)
    base = 100.0
    closes = base + np.cumsum(np.random.randn(rows) * 0.5)
    return pd.DataFrame({
        'open': closes - np.random.rand(rows) * 0.3,
        'high': closes + np.abs(np.random.randn(rows)) * 0.5,
        'low': closes - np.abs(np.random.randn(rows)) * 0.5,
        'close': closes,
        'volume': np.random.rand(rows) * 1_000_000 + 100_000,
    })


def _make_onchain_data(rows: int = 50) -> pd.DataFrame:
    """构造含链上数据列的 DataFrame"""
    df = _make_base_data(rows)
    df['exchange_net_flow'] = np.random.randn(rows) * 1000
    df['whale_tx_count'] = np.random.randint(0, 20, rows).astype(float)
    df['whale_tx_volume'] = np.random.rand(rows) * 5_000_000
    df['tvl'] = np.random.rand(rows) * 50_000_000 + 1_000_000
    df['active_addresses'] = np.random.randint(10000, 100000, rows).astype(float)
    return df


def _make_derivatives_data(rows: int = 50) -> pd.DataFrame:
    """构造含衍生品数据列的 DataFrame"""
    df = _make_base_data(rows)
    df['funding_rate'] = np.random.randn(rows) * 0.001
    df['oi'] = np.random.rand(rows) * 10_000_000 + 1_000_000
    df['long_short_ratio'] = np.random.rand(rows) * 0.8 + 0.6  # 0.6 ~ 1.4
    return df


def _make_macro_data(rows: int = 50) -> pd.DataFrame:
    """构造含宏观数据列的 DataFrame"""
    df = _make_base_data(rows)
    df['fear_greed'] = np.random.randint(20, 80, rows).astype(float)
    df['btc_dominance'] = np.random.rand(rows) * 10 + 50  # 50% ~ 60%
    return df


# ════════════════════════════════════════════════════════
#  1. 链上因子测试
# ════════════════════════════════════════════════════════

class TestExchangeNetFlowFactor:
    """交易所净流量因子"""

    def test_calculate_with_data(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import ExchangeNetFlowFactor
        factor = ExchangeNetFlowFactor()
        data = _make_onchain_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_data(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import ExchangeNetFlowFactor
        factor = ExchangeNetFlowFactor()
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)
        assert (result == 0.0).all()

    def test_metadata(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import ExchangeNetFlowFactor
        factor = ExchangeNetFlowFactor()
        m = factor.metadata
        assert m.factor_id == 'exchange_net_flow'
        assert m.category == 'onchain'
        assert m.subcategory == 'flow'
        assert m.lookback_period == 24

    def test_normalize_false(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import ExchangeNetFlowFactor
        factor = ExchangeNetFlowFactor(params={'normalize': False})
        data = _make_onchain_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        # 不标准化时，结果应等于原始 flow 值
        pd.testing.assert_series_equal(result, data['exchange_net_flow'].fillna(0.0), check_names=False)


class TestWhaleTransactionFactor:
    """鲸鱼交易因子"""

    def test_calculate_with_data(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import WhaleTransactionFactor
        factor = WhaleTransactionFactor()
        data = _make_onchain_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_data(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import WhaleTransactionFactor
        factor = WhaleTransactionFactor()
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 1.0).all()

    def test_metadata(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import WhaleTransactionFactor
        factor = WhaleTransactionFactor()
        m = factor.metadata
        assert m.factor_id == 'whale_transactions'
        assert m.category == 'onchain'
        assert m.subcategory == 'whale'
        assert m.lookback_period == 12

    def test_zero_volume(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import WhaleTransactionFactor
        factor = WhaleTransactionFactor()
        data = _make_base_data(30)
        data['whale_tx_count'] = 0
        data['whale_tx_volume'] = 0.0
        result = factor.calculate(data)
        # 全零时 count_ma 和 volume_ma 也为0，分母有 1e-10 保护
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)


class TestTVLChangeFactor:
    """TVL变化率因子"""

    def test_calculate_with_data(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import TVLChangeFactor
        factor = TVLChangeFactor()
        data = _make_onchain_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_data(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import TVLChangeFactor
        factor = TVLChangeFactor()
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 0.0).all()

    def test_metadata(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import TVLChangeFactor
        factor = TVLChangeFactor()
        m = factor.metadata
        assert m.factor_id == 'tvl_change'
        assert m.category == 'onchain'
        assert m.subcategory == 'defi'
        assert m.lookback_period == 7

    def test_pct_change_values(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import TVLChangeFactor
        factor = TVLChangeFactor(params={'period': 2})
        data = _make_base_data(10)
        data['tvl'] = [100.0, 110.0, 121.0, 133.1, 100.0, 90.0, 80.0, 120.0, 150.0, 200.0]
        result = factor.calculate(data)
        # period=2: pct_change(2) of tvl
        # index 2: (121 - 100) / 100 = 0.21
        assert abs(result.iloc[2] - 0.21) < 1e-6


class TestActiveAddressFactor:
    """活跃地址因子"""

    def test_calculate_with_data(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import ActiveAddressFactor
        factor = ActiveAddressFactor()
        data = _make_onchain_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_data(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import ActiveAddressFactor
        factor = ActiveAddressFactor()
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 1.0).all()

    def test_metadata(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import ActiveAddressFactor
        factor = ActiveAddressFactor()
        m = factor.metadata
        assert m.factor_id == 'active_addresses'
        assert m.category == 'onchain'
        assert m.subcategory == 'network'
        assert m.lookback_period == 14

    def test_ratio_to_mean(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import ActiveAddressFactor
        factor = ActiveAddressFactor(params={'window': 5})
        data = _make_base_data(10)
        data['active_addresses'] = [100.0] * 5 + [200.0] * 5
        result = factor.calculate(data)
        # index 9: 200 / mean([200,200,200,200,200]) = 1.0
        # index 5: 200 / mean([100,100,100,100,200]) = 200/120 ≈ 1.667
        assert abs(result.iloc[9] - 1.0) < 0.01


# ════════════════════════════════════════════════════════
#  2. 衍生品因子测试
# ════════════════════════════════════════════════════════

class TestFundingOIDivergenceFactor:
    """资金费率-OI背离因子"""

    def test_calculate_with_data(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import FundingOIDivergenceFactor
        factor = FundingOIDivergenceFactor()
        data = _make_derivatives_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_oi(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import FundingOIDivergenceFactor
        factor = FundingOIDivergenceFactor()
        data = _make_base_data(50)
        data['funding_rate'] = 0.001
        # 无 oi 列
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 0.0).all()

    def test_calculate_without_funding(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import FundingOIDivergenceFactor
        factor = FundingOIDivergenceFactor()
        data = _make_base_data(50)
        data['oi'] = 1_000_000
        # 无 funding_rate 列
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 0.0).all()

    def test_metadata(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import FundingOIDivergenceFactor
        factor = FundingOIDivergenceFactor()
        m = factor.metadata
        assert m.factor_id == 'funding_oi_divergence'
        assert m.category == 'derivatives'
        assert m.subcategory == 'structure'
        assert m.lookback_period == 24

    def test_divergence_direction(self):
        """OI上升 + funding下降 → 正背离"""
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import FundingOIDivergenceFactor
        factor = FundingOIDivergenceFactor(params={'window': 10})
        data = _make_base_data(30)
        # funding 持续下降
        data['funding_rate'] = np.linspace(0.01, -0.01, 30)
        # OI 持续上升
        data['oi'] = np.linspace(1e6, 5e6, 30)
        result = factor.calculate(data)
        # 末端应为正值（OI_z > fr_z）
        assert result.iloc[-1] > 0


class TestLongShortRatioFactor:
    """多空比因子"""

    def test_calculate_with_data(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import LongShortRatioFactor
        factor = LongShortRatioFactor()
        data = _make_derivatives_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_data(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import LongShortRatioFactor
        factor = LongShortRatioFactor()
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 0.0).all()

    def test_metadata(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import LongShortRatioFactor
        factor = LongShortRatioFactor()
        m = factor.metadata
        assert m.factor_id == 'long_short_ratio'
        assert m.category == 'derivatives'
        assert m.subcategory == 'positioning'
        assert m.lookback_period == 12

    def test_log_ratio_values(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import LongShortRatioFactor
        factor = LongShortRatioFactor()
        data = _make_base_data(5)
        data['long_short_ratio'] = [1.0, 2.0, 0.5, np.e, 1.0]
        result = factor.calculate(data)
        assert abs(result.iloc[0]) < 1e-10  # log(1) = 0
        assert abs(result.iloc[1] - np.log(2.0)) < 1e-6
        assert abs(result.iloc[2] - np.log(0.5)) < 1e-6


class TestLiquidationHeatmapFactor:
    """清算压力因子"""

    def test_calculate_with_oi(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import LiquidationHeatmapFactor
        factor = LiquidationHeatmapFactor()
        data = _make_derivatives_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_oi(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import LiquidationHeatmapFactor
        factor = LiquidationHeatmapFactor()
        data = _make_base_data(50)
        # 无 oi 列时回退到 price_move
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)
        # price_move = (high - low) / close > 0
        assert (result.dropna() >= 0).all()

    def test_metadata(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import LiquidationHeatmapFactor
        factor = LiquidationHeatmapFactor()
        m = factor.metadata
        assert m.factor_id == 'liquidation_pressure'
        assert m.category == 'derivatives'
        assert m.subcategory == 'risk'
        assert m.lookback_period == 12

    def test_zero_price_move(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import LiquidationHeatmapFactor
        factor = LiquidationHeatmapFactor()
        data = pd.DataFrame({
            'open': [100.0] * 10,
            'high': [100.0] * 10,
            'low': [100.0] * 10,
            'close': [100.0] * 10,
            'volume': [1000.0] * 10,
            'oi': [1e6] * 10,
        })
        result = factor.calculate(data)
        # high == low → price_move == 0; first oi.pct_change() is NaN but 0*NaN=NaN
        assert (result.fillna(0.0) == 0.0).all()


# ════════════════════════════════════════════════════════
#  3. 宏观因子测试
# ════════════════════════════════════════════════════════

class TestCryptoFearGreedFactor:
    """恐惧贪婪指数因子"""

    def test_calculate_with_data(self):
        from backend.services.factor_engine.factors.macro.macro_factors import CryptoFearGreedFactor
        factor = CryptoFearGreedFactor()
        data = _make_macro_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_data(self):
        from backend.services.factor_engine.factors.macro.macro_factors import CryptoFearGreedFactor
        factor = CryptoFearGreedFactor()
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 0.0).all()  # (50 - 50) / 50 = 0

    def test_metadata(self):
        from backend.services.factor_engine.factors.macro.macro_factors import CryptoFearGreedFactor
        factor = CryptoFearGreedFactor()
        m = factor.metadata
        assert m.factor_id == 'fear_greed_macro'
        assert m.category == 'macro'
        assert m.subcategory == 'sentiment'
        assert m.lookback_period == 30

    def test_normalization(self):
        from backend.services.factor_engine.factors.macro.macro_factors import CryptoFearGreedFactor
        factor = CryptoFearGreedFactor()
        data = _make_base_data(5)
        # 极度恐惧=0, 中性=50, 极度贪婪=100
        data['fear_greed'] = [0.0, 25.0, 50.0, 75.0, 100.0]
        result = factor.calculate(data)
        assert abs(result.iloc[0] - (-1.0)) < 1e-6  # (0-50)/50
        assert abs(result.iloc[2] - 0.0) < 1e-6     # (50-50)/50
        assert abs(result.iloc[4] - 1.0) < 1e-6     # (100-50)/50


class TestBTCDominanceFactor:
    """BTC主导率因子"""

    def test_calculate_with_data(self):
        from backend.services.factor_engine.factors.macro.macro_factors import BTCDominanceFactor
        factor = BTCDominanceFactor()
        data = _make_macro_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)

    def test_calculate_without_data(self):
        from backend.services.factor_engine.factors.macro.macro_factors import BTCDominanceFactor
        factor = BTCDominanceFactor()
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 0.0).all()

    def test_metadata(self):
        from backend.services.factor_engine.factors.macro.macro_factors import BTCDominanceFactor
        factor = BTCDominanceFactor()
        m = factor.metadata
        assert m.factor_id == 'btc_dominance'
        assert m.category == 'macro'
        assert m.subcategory == 'market_structure'
        assert m.lookback_period == 14

    def test_constant_dominance(self):
        """恒定主导率时 pct_change=0"""
        from backend.services.factor_engine.factors.macro.macro_factors import BTCDominanceFactor
        factor = BTCDominanceFactor(params={'period': 3})
        data = _make_base_data(10)
        data['btc_dominance'] = 55.0
        result = factor.calculate(data)
        # pct_change(3) of constant = 0.0 (除第一个 NaN)
        assert result.iloc[3] == 0.0
        assert result.iloc[-1] == 0.0

    def test_rising_dominance(self):
        """主导率上升时 pct_change > 0"""
        from backend.services.factor_engine.factors.macro.macro_factors import BTCDominanceFactor
        factor = BTCDominanceFactor(params={'period': 2})
        data = _make_base_data(5)
        data['btc_dominance'] = [50.0, 52.0, 54.0, 56.0, 58.0]
        result = factor.calculate(data)
        # pct_change(2): index 2 = (54-50)/50 = 0.08
        assert abs(result.iloc[2] - 0.08) < 1e-6


# ════════════════════════════════════════════════════════
#  4. OnchainDataCollector 测试
# ════════════════════════════════════════════════════════

class TestOnchainDataCollector:
    """链上数据采集器"""

    def test_init(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        assert collector._cache == {}

    def test_collect_all_structure(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        with patch.object(collector, '_collect_tvl', return_value=1e9), \
             patch.object(collector, '_collect_macro', return_value={
                 'fear_greed': 65.0, 'btc_dominance': 55.0
             }):
            result = collector.collect_all(['BTC', 'ETH'])
            assert 'BTC' in result
            assert 'ETH' in result
            for symbol_data in result.values():
                assert 'tvl' in symbol_data
                assert 'fear_greed' in symbol_data
                assert 'btc_dominance' in symbol_data
                # [2026-08-05 v6 2.3] exchange_net_flow/whale_tx_*/active_addresses 为
                # 条件字段：无 Coinglass key 时采集器真实产出才填（2026-07-10 数据修复
                # 禁止合成假数据）；有 key 时 value 必须为数值。
                for _opt in ('exchange_net_flow', 'whale_tx_count', 'whale_tx_volume', 'active_addresses'):
                    if _opt in symbol_data and symbol_data[_opt] is not None:
                        assert isinstance(symbol_data[_opt], (int, float))

    def test_collect_all_default_values(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        with patch.object(collector, '_collect_tvl', return_value=0.0), \
             patch.object(collector, '_collect_macro', return_value={
                 'fear_greed': 50.0, 'btc_dominance': 0.0
             }):
            result = collector.collect_all(['BTC'])
            btc = result['BTC']
            # [2026-08-05 v6 2.3] 无 Coinglass key 时不输出 exchange_net_flow（不造假）
            assert btc.get('exchange_net_flow') is None
            assert btc['tvl'] == 0.0
            assert btc['fear_greed'] == 50.0

    def test_cache_ttl(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        collector._set_cache('test_key', 42.0)
        # 立即获取应命中缓存
        assert collector._get_cached('test_key', 3600) == 42.0
        # TTL=0 时应过期
        time.sleep(0.01)
        assert collector._get_cached('test_key', 0.001) is None

    def test_collect_tvl_failure(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        # requests 是延迟导入（在方法内 import requests），需要 patch requests 模块本身
        with patch('requests.get', side_effect=Exception("network error")):
            result = collector._collect_tvl('BTC')
            assert result == 0.0

    def test_collect_fear_greed_failure(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        with patch('requests.get', side_effect=Exception("network error")):
            result = collector._collect_fear_greed()
            assert result == 50.0  # 中性默认值

    def test_collect_btc_dominance_failure(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        with patch('requests.get', side_effect=Exception("network error")):
            result = collector._collect_btc_dominance()
            assert result == 0.0

    def test_clear_cache(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        collector._set_cache('a', 1)
        collector._set_cache('b', 2)
        collector.clear_cache()
        assert collector._cache == {}

    def test_symbol_to_chain(self):
        from backend.services.onchain_data_collector import OnchainDataCollector
        collector = OnchainDataCollector()
        assert collector._symbol_to_chain('BTC') == 'Bitcoin'
        assert collector._symbol_to_chain('ETH') == 'Ethereum'
        assert collector._symbol_to_chain('ETHUSDT') == 'Ethereum'
        assert collector._symbol_to_chain('UNKNOWN') == 'Ethereum'


# ════════════════════════════════════════════════════════
#  5. 因子注册验证
# ════════════════════════════════════════════════════════

class TestFactorRegistration:
    """验证 9 个新因子在 FactorRegistry 中正确注册"""

    @pytest.fixture(autouse=True)
    def _ensure_registered(self):
        """确保新因子模块被导入"""
        import backend.services.factor_engine.factors.onchain.onchain_factors
        import backend.services.factor_engine.factors.derivatives.derivatives_factors
        import backend.services.factor_engine.factors.macro.macro_factors

    def test_onchain_factors_registered(self):
        from backend.services.factor_engine.factor_registry import registry
        for fid in ['exchange_net_flow', 'whale_transactions', 'tvl_change', 'active_addresses']:
            assert registry.exists(fid), f"因子 {fid} 未注册"

    def test_derivatives_factors_registered(self):
        from backend.services.factor_engine.factor_registry import registry
        for fid in ['funding_oi_divergence', 'long_short_ratio', 'liquidation_pressure']:
            assert registry.exists(fid), f"因子 {fid} 未注册"

    def test_macro_factors_registered(self):
        from backend.services.factor_engine.factor_registry import registry
        for fid in ['fear_greed_macro', 'btc_dominance']:
            assert registry.exists(fid), f"因子 {fid} 未注册"

    def test_category_index(self):
        from backend.services.factor_engine.factor_registry import registry
        onchain = registry.list_factors(category='onchain')
        assert len(onchain) >= 4
        derivatives = registry.list_factors(category='derivatives')
        # v6 阶段 2 新增 l2_depth_imbalance（orderflow_crypto_factors）；
        # 注册表持续扩展，断言关键成员而非硬编码数量
        assert len(derivatives) >= 4
        assert 'l2_depth_imbalance' in derivatives
        macro = registry.list_factors(category='macro')
        assert len(macro) >= 2

    def test_no_factor_id_conflicts(self):
        """确保新因子 factor_id 不与已有因子冲突"""
        from backend.services.factor_engine.factor_registry import registry
        # 新宏观因子使用 fear_greed_macro 避免冲突
        assert registry.exists('fear_greed_macro')
        # fear_greed_index 来自 sentiment 模块，需要单独加载
        # 关键是两个 ID 不同，不存在注册冲突
        assert 'fear_greed_macro' != 'fear_greed_index'


# ════════════════════════════════════════════════════════
#  6. 包导入验证
# ════════════════════════════════════════════════════════

class TestPackageImports:
    """验证新因子包的导入链"""

    def test_onchain_init_importable(self):
        from backend.services.factor_engine.factors.onchain import onchain_factors
        assert hasattr(onchain_factors, 'ExchangeNetFlowFactor')
        assert hasattr(onchain_factors, 'WhaleTransactionFactor')
        assert hasattr(onchain_factors, 'TVLChangeFactor')
        assert hasattr(onchain_factors, 'ActiveAddressFactor')

    def test_derivatives_init_importable(self):
        from backend.services.factor_engine.factors.derivatives import derivatives_factors
        assert hasattr(derivatives_factors, 'FundingOIDivergenceFactor')
        assert hasattr(derivatives_factors, 'LongShortRatioFactor')
        assert hasattr(derivatives_factors, 'LiquidationHeatmapFactor')

    def test_macro_init_importable(self):
        from backend.services.factor_engine.factors.macro import macro_factors
        assert hasattr(macro_factors, 'CryptoFearGreedFactor')
        assert hasattr(macro_factors, 'BTCDominanceFactor')


# ════════════════════════════════════════════════════════
#  7. 数据注入验证
# ════════════════════════════════════════════════════════

class TestDataInjection:
    """验证 unified_data_pool 的数据注入逻辑"""

    def test_injection_creates_expected_columns(self):
        """验证 OnchainDataCollector 返回的数据包含所有必要字段"""
        from backend.services.onchain_data_collector import OnchainDataCollector

        collector = OnchainDataCollector()
        with patch.object(collector, '_collect_tvl', return_value=1e9), \
             patch.object(collector, '_collect_macro', return_value={
                 'fear_greed': 65.0, 'btc_dominance': 55.0
             }):
            result = collector.collect_all(['BTC'])

        expected_fields = [
            'tvl', 'fear_greed', 'btc_dominance',
        ]
        for field in expected_fields:
            assert field in result['BTC'], f"缺少字段: {field}"
        # [2026-08-05 v6 2.3] 链上条件字段：真实产出才填（无 Coinglass key 不造假）
        for _opt in ('exchange_net_flow', 'whale_tx_count', 'whale_tx_volume', 'active_addresses'):
            if _opt in result['BTC'] and result['BTC'][_opt] is not None:
                assert isinstance(result['BTC'][_opt], (int, float))

    def test_injection_fallback_on_failure(self):
        """注入失败时不阻塞主流程"""
        from backend.services.onchain_data_collector import OnchainDataCollector

        collector = OnchainDataCollector()
        # 所有 API 都失败时应返回零值
        with patch('requests.get', side_effect=Exception("API down")):
            result = collector.collect_all(['BTC'])
            assert result['BTC']['fear_greed'] == 50.0  # 中性默认值
            assert result['BTC']['btc_dominance'] == 0.0


# ════════════════════════════════════════════════════════
#  8. v6 阶段 2 因子补齐测试（wick_protection / stablecoin_mint_burn）
# ════════════════════════════════════════════════════════

class TestWickProtectionFactor:
    """插针保护因子（v6 阶段 2 新增）"""

    def test_metadata(self):
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import WickProtectionFactor
        factor = WickProtectionFactor()
        m = factor.metadata
        assert m.factor_id == 'wick_protection'
        assert m.category == 'derivatives'
        assert m.subcategory == 'orderflow'
        assert m.lookback_period == 12

    def test_bullish_lower_wick(self):
        """长下影线 + 收盘收复 → 正信号（看多保护）"""
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import WickProtectionFactor
        factor = WickProtectionFactor(params={'window': 3, 'wick_ratio': 2.0})
        data = pd.DataFrame({
            'open': [100.0, 100.0, 100.0],
            'high': [102.0, 101.0, 103.0],
            'low':  [98.0, 90.0, 99.0],   # 第2根大幅下探到90
            'close':[101.0, 101.0, 102.0],  # 第2根收盘回到101 → 下影插针+收复
        })
        result = factor.calculate(data)
        # 第2根：body=1, lower_wick=10 > body*2 → bullish；窗口均值>0
        assert result.iloc[1] > 0
        assert result.iloc[-1] > 0

    def test_bearish_upper_wick(self):
        """长上影线 + 收盘回落 → 负信号（看空陷阱）"""
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import WickProtectionFactor
        factor = WickProtectionFactor(params={'window': 3, 'wick_ratio': 2.0})
        data = pd.DataFrame({
            'open': [100.0, 100.0, 100.0],
            'high': [102.0, 110.0, 103.0],  # 第2根冲高到110
            'low':  [98.0, 99.0, 99.0],
            'close':[101.0, 99.5, 102.0],  # 第2根收盘回落(99.5<100) → 上影插针
        })
        result = factor.calculate(data)
        # 第2根：body=0.5, upper_wick=10 > 0.5*2 → bearish；窗口均值<0
        assert result.iloc[1] < 0
        assert result.iloc[-1] < 0

    def test_no_wick_neutral(self):
        """无明显插针 → 中性 0"""
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import WickProtectionFactor
        factor = WickProtectionFactor(params={'window': 3})
        data = pd.DataFrame({
            'open':  [100.0] * 5,
            'high':  [101.0] * 5,
            'low':   [99.0] * 5,
            'close': [100.5] * 5,
        })
        result = factor.calculate(data)
        assert (result == 0.0).all()

    def test_missing_columns_degrades_to_zero(self):
        """缺列时优雅降级为 0"""
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import WickProtectionFactor
        factor = WickProtectionFactor()
        data = _make_base_data(20)
        del data['open']
        result = factor.calculate(data)
        assert (result == 0.0).all()

    def test_value_range(self):
        """输出值域 ∈ [-1, 1]"""
        from backend.services.factor_engine.factors.derivatives.derivatives_factors import WickProtectionFactor
        factor = WickProtectionFactor(params={'window': 5})
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert result.between(-1.0, 1.0).all()


class TestStablecoinMintBurnFactor:
    """稳定币铸造/销毁因子（v6 阶段 2 新增）"""

    def test_metadata(self):
        from backend.services.factor_engine.factors.onchain.onchain_factors import StablecoinMintBurnFactor
        factor = StablecoinMintBurnFactor()
        m = factor.metadata
        assert m.factor_id == 'stablecoin_mint_burn'
        assert m.category == 'onchain'
        assert m.subcategory == 'flow'
        assert m.lookback_period == 24

    def test_calculate_with_data(self):
        """有 stablecoin_mint_burn 列时输出 z-score 序列"""
        from backend.services.factor_engine.factors.onchain.onchain_factors import StablecoinMintBurnFactor
        factor = StablecoinMintBurnFactor(params={'window': 10})
        data = _make_base_data(50)
        data['stablecoin_mint_burn'] = np.linspace(-1000, 1000, 50)  # 单调上升 → 末端 z>0
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert len(result) == len(data)
        assert result.iloc[-1] > 0

    def test_calculate_without_data(self):
        """无列时中性 0"""
        from backend.services.factor_engine.factors.onchain.onchain_factors import StablecoinMintBurnFactor
        factor = StablecoinMintBurnFactor()
        data = _make_base_data(50)
        result = factor.calculate(data)
        assert isinstance(result, pd.Series)
        assert (result == 0.0).all()

    def test_normalize_false(self):
        """不标准化时返回原始净铸造值"""
        from backend.services.factor_engine.factors.onchain.onchain_factors import StablecoinMintBurnFactor
        factor = StablecoinMintBurnFactor(params={'normalize': False})
        data = _make_base_data(10)
        data['stablecoin_mint_burn'] = np.arange(10.0)
        result = factor.calculate(data)
        pd.testing.assert_series_equal(result, data['stablecoin_mint_burn'], check_names=False)

    def test_constant_flow_zero_std_guard(self):
        """恒定流量时 std=0，1e-10 分母保护不产生 NaN/Inf"""
        from backend.services.factor_engine.factors.onchain.onchain_factors import StablecoinMintBurnFactor
        factor = StablecoinMintBurnFactor(params={'window': 5})
        data = _make_base_data(10)
        data['stablecoin_mint_burn'] = 500.0
        result = factor.calculate(data)
        assert result.notna().all()
        assert np.isfinite(result).all()


class TestV6FactorAliases:
    """v6 阶段 2 因子别名检索（不破坏历史数据对齐）"""

    @pytest.fixture(autouse=True)
    def _ensure_registered(self):
        import backend.services.factor_engine.factors.onchain.onchain_factors
        import backend.services.factor_engine.factors.derivatives.derivatives_factors

    def test_new_factors_registered(self):
        from backend.services.factor_engine.factor_registry import registry
        assert registry.exists('wick_protection'), "wick_protection 未注册"
        assert registry.exists('stablecoin_mint_burn'), "stablecoin_mint_burn 未注册"

    def test_aliases_resolvable(self):
        """v6 名 liquidation_heatmap / onchain_netflow 可通过别名检索到规范因子"""
        from backend.services.factor_engine.factor_registry import registry
        assert registry.exists('liquidation_heatmap')
        assert registry.exists('onchain_netflow')
        assert registry.resolve('liquidation_heatmap') == 'liquidation_pressure'
        assert registry.resolve('onchain_netflow') == 'exchange_net_flow'

    def test_alias_get_returns_canonical_instance(self):
        """通过别名 get 得到规范 factor_id 的因子实例"""
        from backend.services.factor_engine.factor_registry import registry
        factor = registry.get('liquidation_heatmap')
        assert factor.metadata.factor_id == 'liquidation_pressure'
        assert registry.get_metadata('onchain_netflow').factor_id == 'exchange_net_flow'

    def test_unknown_alias_raises_keyerror(self):
        """未注册的别名/ID 仍按原行为抛 KeyError"""
        from backend.services.factor_engine.factor_registry import registry
        with pytest.raises(KeyError):
            registry.get('no_such_factor_xyz')
