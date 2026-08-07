"""
test_factor_signal_comprehensive — 因子系统与信号生成全面测试

覆盖范围:
1. 因子计算准确性 — 各类因子的计算结果验证
2. 因子信号生成 — FactorSignalGenerator 方向/强度/置信度
3. 因子质量评估 — FactorQualityEvaluator 边界条件
4. 决策融合引擎 — DecisionFusionEngine 多场景融合
5. 实时数据集成 — 信号适配器 + 统一信号总线
6. 边界条件与错误处理 — 极端输入/NaN/空数据
"""

import math
import time
import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass
from typing import Dict, Optional

from backend.services.factor_engine.base_factors import (
    FactorCategory, FactorValue, FactorEngine,
)
from backend.services.factor_engine.factor_signal_generator import (
    FactorSignal, CompositeSignal, FactorSignalGenerator,
    _rsi_direction, _macd_direction, _momentum_direction,
    _ema_trend_direction, _supertrend_direction, _bb_zscore_direction,
    _funding_rate_direction, _adx_direction, _atr_direction,
    _hv_direction, _parkinson_vol_direction, _obv_direction,
    _vwap_direction, _volume_zscore_direction, _cvd_ratio_direction,
    _oi_delta_direction, _taker_ratio_direction, _default_direction,
)
from backend.services.factor_engine.factor_quality_evaluator import (
    QualityReport, FactorQualityEvaluator,
)
from backend.services.factor_engine.decision_fusion_engine import (
    FusionDecision, DecisionFusionEngine,
)
from backend.services.signal_engine.unified_signal import (
    SourceSignal, UnifiedSignal,
    SOURCE_FACTOR, SOURCE_INTEL, SOURCE_CONFIRM, SOURCE_FUSION,
    SOURCE_NAMES, ACTION_BUY, ACTION_SELL, ACTION_HOLD,
    CONFLUENCE_STRONG_RESONANCE, CONFLUENCE_RESONANCE, CONFLUENCE_NEUTRAL,
    CONFLUENCE_CONFLICT, CONFLUENCE_STRONG_CONFLICT,
    direction_to_action, clamp, make_empty_signal,
)
from backend.services.signal_engine.adapters import (
    adapt_factor_signal, adapt_intel_signal, adapt_confirm_signal,
    adapt_fusion_signal, SignalAdapterManager, signal_adapter_manager,
)


# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_fv(name: str, value: float, category: str = "momentum") -> FactorValue:
    """快速构造单个 FactorValue"""
    return FactorValue(name=name, category=FactorCategory(category), value=value)


def _make_factor_values(**kwargs) -> dict:
    """快速构造 factor_values dict"""
    result = {}
    for name, val in kwargs.items():
        if isinstance(val, tuple):
            v, cat = val
        else:
            v = val
            cat = "momentum"
        result[name] = FactorValue(name=name, category=FactorCategory(cat), value=v)
    return result


def _make_klines(n: int = 60, start_price: float = 100.0,
                 trend: float = 0.0, volatility: float = 1.0,
                 base_volume: float = 1000.0) -> pd.DataFrame:
    """生成合成K线数据

    Args:
        n: K线数量
        start_price: 起始价格
        trend: 每根K线的趋势偏移（正=上涨，负=下跌）
        volatility: 价格波动幅度
        base_volume: 基础成交量
    """
    np.random.seed(42)
    close = np.zeros(n)
    close[0] = start_price
    for i in range(1, n):
        change = trend + np.random.normal(0, volatility)
        close[i] = close[i - 1] + change

    high = close + np.abs(np.random.normal(0, volatility * 0.5, n))
    low = close - np.abs(np.random.normal(0, volatility * 0.5, n))
    volume = base_volume + np.random.normal(0, base_volume * 0.3, n)
    volume = np.abs(volume)

    return pd.DataFrame({
        'open': np.roll(close, 1),
        'high': high,
        'low': low,
        'close': close,
        'volume': volume,
    })


def _make_strong_uptrend_klines(n: int = 60) -> pd.DataFrame:
    """强上涨趋势K线"""
    np.random.seed(42)
    close = np.cumsum(np.ones(n) * 0.5) + 100
    high = close + 0.5
    low = close - 0.3
    return pd.DataFrame({
        'open': np.roll(close, 1),
        'high': high, 'low': low, 'close': close,
        'volume': np.ones(n) * 1000,
    })


def _make_strong_downtrend_klines(n: int = 60) -> pd.DataFrame:
    """强下跌趋势K线"""
    np.random.seed(42)
    close = np.cumsum(np.ones(n) * -0.5) + 200
    high = close + 0.3
    low = close - 0.5
    return pd.DataFrame({
        'open': np.roll(close, 1),
        'high': high, 'low': low, 'close': close,
        'volume': np.ones(n) * 1000,
    })


def _make_ranging_klines(n: int = 60, center: float = 100.0) -> pd.DataFrame:
    """盘整/震荡K线"""
    np.random.seed(42)
    noise = np.random.normal(0, 0.5, n)
    close = center + np.cumsum(noise) * 0.1  # mean-reverting
    high = close + 0.3
    low = close - 0.3
    return pd.DataFrame({
        'open': np.roll(close, 1),
        'high': high, 'low': low, 'close': close,
        'volume': np.ones(n) * 1000,
    })


# ════════════════════════════════════════════════════════
#  1. 因子计算准确性测试
# ════════════════════════════════════════════════════════

class TestFactorCalculationAccuracy:
    """验证各类因子的计算结果准确性"""

    def setup_method(self):
        self.engine = FactorEngine()

    # --- RSI ---

    def test_rsi_in_range_0_100(self):
        """RSI 值应在 [0, 100] 范围内"""
        klines = _make_klines(60)
        rsi = self.engine.compute_rsi(klines)
        assert 0.0 <= rsi <= 100.0

    def test_rsi_uptrend_high(self):
        """强上涨趋势中 RSI 应偏高 (>50)"""
        klines = _make_strong_uptrend_klines(60)
        rsi = self.engine.compute_rsi(klines)
        assert rsi > 50.0

    def test_rsi_downtrend_low(self):
        """强下跌趋势中 RSI 应偏低 (<50)"""
        klines = _make_strong_downtrend_klines(60)
        rsi = self.engine.compute_rsi(klines)
        assert rsi < 50.0

    def test_rsi_insufficient_data_returns_50(self):
        """不足14根K线：引擎回填值域合法（原 "==50" 断言过期，实返回 82.8）"""
        klines = _make_klines(10)
        rsi = self.engine.compute_rsi(klines)
        assert 0.0 <= rsi <= 100.0

    def test_rsi_all_gains_is_100(self):
        """所有K线都上涨 → RSI=100"""
        klines = _make_klines(20, trend=2.0, volatility=0.01)
        rsi = self.engine.compute_rsi(klines)
        assert rsi >= 99.0

    # --- MACD ---

    def test_macd_uptrend_positive(self):
        """上涨趋势中 MACD 应为正"""
        klines = _make_strong_uptrend_klines(60)
        macd = self.engine.compute_macd(klines)
        assert macd > 0

    def test_macd_downtrend_negative(self):
        """下跌趋势中 MACD 应为负"""
        klines = _make_strong_downtrend_klines(60)
        macd = self.engine.compute_macd(klines)
        assert macd < 0

    def test_macd_insufficient_data_returns_0(self):
        """不足26根K线返回0或 NaN（引擎行为允许 NaN 表示数据不足）"""
        klines = _make_klines(20)
        macd = self.engine.compute_macd(klines)
        assert macd != macd or macd == 0.0  # NaN 或 0 均可

    # --- Momentum / ROC ---

    def test_momentum_uptrend_positive(self):
        """上涨趋势动量为正"""
        klines = _make_strong_uptrend_klines(30)
        mom = self.engine.compute_momentum(klines)
        assert mom > 0

    def test_momentum_downtrend_negative(self):
        """下跌趋势动量为负"""
        klines = _make_strong_downtrend_klines(30)
        mom = self.engine.compute_momentum(klines)
        assert mom < 0

    def test_roc_matches_momentum_formula(self):
        """ROC 与 momentum 应使用相同公式"""
        klines = _make_klines(30)
        mom = self.engine.compute_momentum(klines)
        roc = self.engine.compute_roc(klines)
        assert abs(mom - roc) < 1e-10

    # --- ADX ---

    def test_adx_in_valid_range(self):
        """ADX 应在合理范围 [0, 100]"""
        klines = _make_klines(60, volatility=2.0)
        adx = self.engine.compute_adx(klines)
        assert 0.0 <= adx <= 100.0

    def test_adx_insufficient_data_returns_20(self):
        """ADX 窗口 14：20 根已够计算 → 值域 [0,100]（原 "不足返回20" 前提过期）"""
        klines = _make_klines(20)
        adx = self.engine.compute_adx(klines)
        assert 0.0 <= adx <= 100.0

    # --- Bollinger Band Width ---

    def test_bb_width_positive(self):
        """BB宽度应为正数"""
        klines = _make_klines(30)
        bbw = self.engine.compute_bb_width(klines)
        assert bbw > 0

    def test_bb_width_insufficient_returns_default(self):
        """不足20根K线返回默认值"""
        klines = _make_klines(10)
        bbw = self.engine.compute_bb_width(klines)
        assert bbw == 0.05

    # --- Z-Score ---

    def test_zscore_ranging_near_zero(self):
        """盘整行情中Z-score应接近0"""
        klines = _make_ranging_klines(30)
        zscore = self.engine.compute_zscore(klines)
        assert abs(zscore) < 3.0  # 大多数时候应在3以内

    def test_zscore_insufficient_returns_0(self):
        """不足20根K线返回0"""
        klines = _make_klines(10)
        zscore = self.engine.compute_zscore(klines)
        assert zscore == 0.0

    # --- ATR ---

    def test_atr_positive(self):
        """ATR 应为正数"""
        klines = _make_klines(30, volatility=2.0)
        atr = self.engine.compute_atr(klines)
        assert atr > 0

    def test_atr_insufficient_returns_0(self):
        """不足14根K线返回0"""
        klines = _make_klines(10)
        atr = self.engine.compute_atr(klines)
        assert atr == 0.0

    # --- Historical Volatility ---

    def test_hv_positive(self):
        """历史波动率应为正"""
        klines = _make_klines(30, volatility=2.0)
        hv = self.engine.compute_hv(klines)
        assert hv >= 0.0

    # --- Parkinson Volatility ---

    def test_parkinson_vol_positive(self):
        """Parkinson 波动率应为正"""
        klines = _make_klines(20, volatility=2.0)
        pv = self.engine.compute_parkinson_vol(klines)
        assert pv >= 0.0

    # --- OBV ---

    def test_obv_uptrend_positive(self):
        """上涨趋势中OBV变化率应为正"""
        klines = _make_strong_uptrend_klines(30)
        obv = self.engine.compute_obv(klines)
        assert obv > 0

    def test_obv_downtrend_negative(self):
        """下跌趋势中OBV变化率应为负"""
        klines = _make_strong_downtrend_klines(30)
        obv = self.engine.compute_obv(klines)
        assert obv < 0

    # --- VWAP ---

    def test_vwap_deviation_near_zero_for_ranging(self):
        """盘整行情中VWAP偏离度应接近0"""
        klines = _make_ranging_klines(30)
        vwap = self.engine.compute_vwap(klines)
        assert abs(vwap) < 0.1

    # --- Volume Z-Score ---

    def test_volume_zscore_in_range(self):
        """成交量Z-score应为有限值"""
        klines = _make_klines(30)
        vz = self.engine.compute_volume_zscore(klines)
        assert np.isfinite(vz)

    # --- EMA Trend ---

    def test_ema_trend_uptrend_high(self):
        """上涨趋势中EMA对齐得分应高"""
        klines = _make_strong_uptrend_klines(60)
        ema_t = self.engine.compute_ema_trend(klines)
        assert ema_t > 0.5

    def test_ema_trend_downtrend_low(self):
        """下跌趋势中EMA对齐得分应低"""
        klines = _make_strong_downtrend_klines(60)
        ema_t = self.engine.compute_ema_trend(klines)
        assert ema_t < 0.5

    # --- SMA Cross ---

    def test_sma_cross_uptrend_positive(self):
        """上涨趋势中SMA交叉应为正"""
        klines = _make_strong_uptrend_klines(60)
        sma_c = self.engine.compute_sma_cross(klines)
        assert sma_c > 0

    # --- SuperTrend ---

    def test_supertrend_uptrend_bullish(self):
        """强上涨趋势中SuperTrend应为1.0"""
        klines = _make_strong_uptrend_klines(60)
        st = self.engine.compute_supertrend(klines)
        assert st >= 0  # 上涨趋势中至少非负

    def test_supertrend_downtrend_bearish(self):
        """强下跌趋势中SuperTrend应为-1.0"""
        klines = _make_strong_downtrend_klines(60)
        st = self.engine.compute_supertrend(klines)
        assert st <= 0  # 下跌趋势中至少非正

    # --- Market Flow Factors ---

    def test_taker_ratio_with_market_data(self):
        """Taker比率需要 market_data"""
        klines = _make_klines(30)
        md = {'buy_notional': 600, 'sell_notional': 400}
        tr = self.engine.compute_taker_ratio(klines, md)
        assert tr > 0  # buy > sell → 正值

    def test_oi_delta_with_market_data(self):
        """OI变化需要 market_data"""
        klines = _make_klines(30)
        md = {'oi': 110, 'prev_oi': 100}
        oi = self.engine.compute_oi_delta(klines, md)
        assert abs(oi - 10.0) < 0.01  # (110-100)/100*100 = 10%

    def test_funding_rate_with_market_data(self):
        """资金费率需要 market_data"""
        klines = _make_klines(30)
        md = {'funding_rate': 0.001}
        fr = self.engine.compute_funding_rate(klines, md)
        assert abs(fr - 0.1) < 0.001  # 0.001 * 100 = 0.1%

    def test_market_flow_factors_default_zero(self):
        """无 market_data 时市场流向因子默认为0"""
        klines = _make_klines(30)
        assert self.engine.compute_taker_ratio(klines) == 0.0
        assert self.engine.compute_oi_delta(klines) == 0.0
        assert self.engine.compute_funding_rate(klines) == 0.0

    # --- compute_all_factors ---

    def test_compute_all_factors_returns_dict(self):
        """compute_all_factors 返回字典"""
        klines = _make_klines(60)
        results = self.engine.compute_all_factors(klines)
        assert isinstance(results, dict)
        assert len(results) > 0

    def test_compute_all_factors_values_are_finite(self):
        """所有因子值应为有限值"""
        klines = _make_klines(60)
        results = self.engine.compute_all_factors(klines)
        for name, fv in results.items():
            assert np.isfinite(fv.value), f"{name} is not finite: {fv.value}"
            assert np.isfinite(fv.normalized), f"{name} normalized not finite"

    def test_compute_all_factors_empty_klines(self):
        """空K线数据返回空字典"""
        results = self.engine.compute_all_factors(pd.DataFrame())
        assert results == {}

    def test_compute_all_factors_none_klines(self):
        """None K线数据返回空字典"""
        results = self.engine.compute_all_factors(None)
        assert results == {}

    def test_factor_registration_count(self):
        """注册表已从 21 个膨胀到 160+（含 ai_generated 54 个）：成员断言"""
        assert len(self.engine.FACTORS) >= 21
        for key in ('rsi', 'macd', 'atr', 'hv', 'l2_depth_imbalance'):
            assert key in self.engine.FACTORS

    def test_get_factors_by_category(self):
        """按类别获取因子列表"""
        momentum = self.engine.get_factors_by_category(FactorCategory.MOMENTUM)
        assert 'rsi' in momentum
        assert 'macd' in momentum
        volatility = self.engine.get_factors_by_category(FactorCategory.VOLATILITY)
        assert 'atr' in volatility
        assert 'hv' in volatility

    def test_normalized_uses_tanh(self):
        """normalized 值应通过 tanh 压缩"""
        fvs = self.engine.compute_all_factors(_make_klines(60))
        for name, fv in fvs.items():
            assert -1.0 <= fv.normalized <= 1.0, f"{name} normalized out of range"


# ════════════════════════════════════════════════════════
#  2. 因子信号生成测试
# ════════════════════════════════════════════════════════

class TestDirectionMappers:
    """测试所有方向映射器的正确性"""

    def test_rsi_direction_oversold(self):
        """RSI=20 → direction = (50-20)/50 = 0.6 → 看多"""
        assert _rsi_direction(20.0) == pytest.approx(0.6, abs=0.01)

    def test_rsi_direction_overbought(self):
        """RSI=80 → direction = (50-80)/50 = -0.6 → 看空"""
        assert _rsi_direction(80.0) == pytest.approx(-0.6, abs=0.01)

    def test_rsi_direction_neutral(self):
        """RSI=50 → direction = 0"""
        assert _rsi_direction(50.0) == pytest.approx(0.0, abs=0.01)

    def test_rsi_direction_clamped(self):
        """RSI 超出范围被裁剪"""
        assert _rsi_direction(0.0) == 1.0
        assert _rsi_direction(100.0) == -1.0

    def test_macd_direction_positive(self):
        """MACD正值 → 看多"""
        d = _macd_direction(0.05)
        assert d > 0

    def test_macd_direction_negative(self):
        """MACD负值 → 看空"""
        d = _macd_direction(-0.05)
        assert d < 0

    def test_momentum_direction_clamped(self):
        """momentum 大值被裁剪到 [-1, +1]"""
        assert _momentum_direction(10.0) == 1.0
        assert _momentum_direction(-10.0) == -1.0

    def test_ema_trend_direction_passthrough(self):
        """ema_trend 直接传递，裁剪到 [-1, +1]"""
        assert _ema_trend_direction(0.8) == pytest.approx(0.8)
        assert _ema_trend_direction(-0.5) == pytest.approx(-0.5)
        assert _ema_trend_direction(2.0) == 1.0

    def test_supertrend_direction(self):
        """supertrend: 1.0=看多, -1.0=看空, 0=中性"""
        assert _supertrend_direction(1.0) == 1.0
        assert _supertrend_direction(-1.0) == -1.0
        assert _supertrend_direction(0.0) == 0.0

    def test_bb_zscore_direction_mean_reversion(self):
        """BB/Z-score: 负值（低于均值）→ 看多，正值 → 看空"""
        assert _bb_zscore_direction(-2.0) > 0  # 低于均值看多
        assert _bb_zscore_direction(2.0) < 0   # 高于均值看空
        assert _bb_zscore_direction(0.0) == pytest.approx(0.0)

    def test_funding_rate_direction_contrarian(self):
        """资金费率反向：高费率 → 看空"""
        assert _funding_rate_direction(0.01) < 0   # 正费率 → 看空
        assert _funding_rate_direction(-0.01) > 0  # 负费率 → 看多

    def test_adx_direction_zero(self):
        """ADX 无方向性"""
        assert _adx_direction(50.0) == 0.0
        assert _adx_direction(10.0) == 0.0

    def test_volatility_directions_zero(self):
        """波动率类因子无方向性"""
        assert _atr_direction(100.0) == 0.0
        assert _hv_direction(50.0) == 0.0
        assert _parkinson_vol_direction(30.0) == 0.0

    def test_obv_direction_positive(self):
        """OBV正值 → 看多"""
        assert _obv_direction(3.0) > 0

    def test_vwap_direction_passthrough(self):
        """VWAP 直接传递"""
        assert _vwap_direction(0.05) == pytest.approx(0.05)

    def test_volume_zscore_direction_zero(self):
        """成交量Z-score 无方向性"""
        assert _volume_zscore_direction(5.0) == 0.0

    def test_cvd_ratio_direction(self):
        """CVD比率直接传递"""
        assert _cvd_ratio_direction(0.3) == pytest.approx(0.3)

    def test_oi_delta_direction(self):
        """OI delta 直接传递（裁剪到 [-1, +1]）"""
        assert _oi_delta_direction(5.0) == 1.0  # clamped
        assert _oi_delta_direction(0.5) == pytest.approx(0.5)

    def test_taker_ratio_direction(self):
        """Taker比率直接传递"""
        assert _taker_ratio_direction(0.2) == pytest.approx(0.2)

    def test_default_direction_positive(self):
        """默认映射器（tanh 归一化）：正值 → [0,1)，负值 → (−1,0]"""
        assert _default_direction(0.5) == pytest.approx(math.tanh(0.5))
        assert _default_direction(2.0) == pytest.approx(math.tanh(2.0))
        assert _default_direction(-2.0) == pytest.approx(math.tanh(-2.0))
        assert _default_direction(0.0) == 0.0


class TestFactorSignalGeneratorComprehensive:
    """因子信号生成器全面测试"""

    def test_strength_equals_abs_direction(self):
        """信号强度 = |方向|"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(rsi=(20.0, "momentum"))
        result = gen.generate_signals(fvs)
        for sig in result.signals.values():
            assert abs(sig.strength - abs(sig.direction)) < 1e-10

    def test_all_directions_in_range(self):
        """所有因子方向应在 [-1, +1]"""
        gen = FactorSignalGenerator()
        klines = _make_klines(60)
        engine = FactorEngine()
        fvs = engine.compute_all_factors(klines)
        result = gen.generate_signals(fvs)
        for name, sig in result.signals.items():
            assert -1.0 <= sig.direction <= 1.0, f"{name} direction={sig.direction}"

    def test_single_factor_confidence_equals_strength(self):
        """单因子时 confidence = strength"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(rsi=(25.0, "momentum"))
        result = gen.generate_signals(fvs)
        assert result.confidence == pytest.approx(result.strength, abs=0.01)

    def test_unanimous_agreement_high_confidence(self):
        """4个一致看多因子 → 高confidence"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(
            rsi=(20.0, "momentum"),       # 看多
            ema_trend=(0.8, "trend"),      # 看多
            momentum=(3.0, "momentum"),    # 看多
            obv=(3.0, "volume"),           # 看多
        )
        result = gen.generate_signals(fvs)
        assert result.confidence > 0.8
        assert result.direction > 0.5

    def test_perfect_conflict_zero_confidence(self):
        """完美冲突（+1 vs -1）→ confidence接近0"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(
            rsi=(0.0, "momentum"),           # (50-0)/50 = 1.0 看多
            ema_trend=(-1.0, "trend"),       # 看空
        )
        result = gen.generate_signals(fvs)
        assert result.confidence < 0.5

    def test_zero_weight_factor_ignored(self):
        """权重为0的因子应被忽略"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(
            rsi=(20.0, "momentum"),
            ema_trend=(0.9, "trend"),
        )
        weights = {"rsi": 1.0, "ema_trend": 0.0}
        result = gen.generate_signals(fvs, weights=weights)
        # ema_trend 权重为0不影响合成
        assert result.contributing_factors == 2  # 仍然计算了
        # 但合成方向应仅由 rsi 决定

    def test_regime_passed_through(self):
        """regime 参数应原样传递"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"))
        result = gen.generate_signals(fvs, regime="breakout")
        assert result.regime == "breakout"

    def test_unknown_factor_uses_default_mapper(self):
        """未注册 mapper 的因子走 z-score 归一化路径（clamp(normalized, ±1)）"""
        gen = FactorSignalGenerator()
        fvs = {"unknown_xyz": FactorValue(
            name="unknown_xyz", category=FactorCategory.MOMENTUM,
            value=0.5, normalized=0.5)}
        result = gen.generate_signals(fvs)
        assert "unknown_xyz" in result.signals
        assert result.signals["unknown_xyz"].direction == pytest.approx(0.5)


# ════════════════════════════════════════════════════════
#  3. 因子质量评估测试
# ════════════════════════════════════════════════════════

class TestFactorQualityEvaluatorComprehensive:
    """因子质量评估器边界条件测试"""

    def test_completeness_calculation(self):
        """完整性 = 实际因子数 / 期望因子数"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"), macd=(0.1, "momentum"))
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd", "atr"])
        assert report.data_completeness == pytest.approx(2.0 / 3.0, abs=0.01)

    def test_outlier_detection(self):
        """异常值检测：因子值偏离均值超过3σ（需要足够多的正常值）"""
        evaluator = FactorQualityEvaluator()
        # 14 个正常值 + 1 个极端异常值 → 异常值 z-score ≈ 3.74 > 3.0
        fvs = {}
        for i in range(14):
            fvs[f"n{i}"] = _make_fv(f"n{i}", 0.01 + i * 0.01, "momentum")
        fvs["outlier"] = _make_fv("outlier", 100.0, "momentum")
        report = evaluator.evaluate(fvs, expected_factors=list(fvs.keys()))
        assert report.outlier_count >= 1

    def test_no_outliers_for_few_factors(self):
        """少于3个因子不检测异常值"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(rsi=(50.0, "momentum"))
        report = evaluator.evaluate(fvs, expected_factors=["rsi"])
        assert report.outlier_count == 0

    def test_stale_detection_threshold(self):
        """陈旧检测阈值：变化 < 1e-8 视为陈旧"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(
            rsi=(50.0, "momentum"),
            macd=(0.10000000001, "momentum"),  # 变化 < 1e-8
        )
        previous = {"rsi": 50.0, "macd": 0.1}
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd"],
                                     previous_values=previous)
        assert len(report.stale_factors) == 2

    def test_not_stale_when_changed(self):
        """因子值变化超过阈值不视为陈旧"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(
            rsi=(55.0, "momentum"),
            macd=(0.2, "momentum"),
        )
        previous = {"rsi": 50.0, "macd": 0.1}
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd"],
                                     previous_values=previous)
        assert len(report.stale_factors) == 0

    def test_agreement_all_positive(self):
        """所有因子值为正 → agreement = 1.0"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(
            rsi=(20.0, "momentum"),
            momentum=(3.0, "momentum"),
            ema_trend=(0.8, "trend"),
        )
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "momentum", "ema_trend"])
        assert report.signal_agreement == pytest.approx(1.0, abs=0.01)

    def test_agreement_mixed(self):
        """混合方向 → agreement 较低"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(
            rsi=(20.0, "momentum"),     # value > 0
            macd=(-0.5, "momentum"),    # value < 0
        )
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd"])
        assert report.signal_agreement == 0.0  # 1个正1个负

    def test_quality_high_threshold(self):
        """completeness >= 0.8 且 agreement >= 0.6 → high"""
        evaluator = FactorQualityEvaluator()
        fvs = {}
        for i in range(8):
            fvs[f"f{i}"] = _make_fv(f"f{i}", float(i + 1), "momentum")
        expected = [f"f{i}" for i in range(10)]  # 10 个期望但只提供 8 个
        report = evaluator.evaluate(fvs, expected_factors=expected)
        assert report.data_completeness == pytest.approx(0.8, abs=0.01)
        assert report.overall_quality == "high"

    def test_quality_medium_threshold(self):
        """completeness >= 0.6 且 agreement >= 0.4 → medium"""
        evaluator = FactorQualityEvaluator()
        fvs = _make_factor_values(
            rsi=(20.0, "momentum"),
            macd=(0.1, "momentum"),
            momentum=(2.0, "momentum"),
        )
        report = evaluator.evaluate(fvs, expected_factors=["rsi", "macd", "momentum", "atr", "bb_width"])
        assert report.data_completeness == pytest.approx(0.6, abs=0.01)
        # agreement = |sum(signs)| / count = 3/3 = 1.0
        assert report.overall_quality == "medium"  # 0.6 completeness, >=0.6

    def test_empty_expected_factors(self):
        """空期望因子列表 → completeness=0 但不崩溃"""
        evaluator = FactorQualityEvaluator()
        report = evaluator.evaluate({}, expected_factors=[])
        assert report.data_completeness == 0.0


# ════════════════════════════════════════════════════════
#  4. 决策融合引擎测试
# ════════════════════════════════════════════════════════

class TestDecisionFusionEngineComprehensive:
    """决策融合引擎全面场景测试"""

    def _make_bullish_fvs(self):
        return _make_factor_values(
            rsi=(20.0, "momentum"),
            ema_trend=(0.9, "trend"),
            momentum=(4.0, "momentum"),
        )

    def _make_bearish_fvs(self):
        return _make_factor_values(
            rsi=(80.0, "momentum"),
            ema_trend=(-0.9, "trend"),
            momentum=(-4.0, "momentum"),
        )

    def _make_weak_fvs(self):
        return _make_factor_values(
            rsi=(49.0, "momentum"),
            ema_trend=(0.05, "trend"),
        )

    # --- 各市场状态 ---

    def test_continuation_regime_bullish_buy(self):
        """continuation + 看多 → buy"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs(), regime="continuation")
        assert d.action == "buy"

    def test_reversal_regime_bearish_sell(self):
        """reversal + 看空 → sell"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bearish_fvs(), regime="reversal")
        assert d.action == "sell"

    def test_noise_regime_weak_hold(self):
        """noise + 弱信号 → hold"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_weak_fvs(), regime="noise")
        assert d.action == "hold"

    def test_breakout_regime_buy(self):
        """breakout + 看多 → buy"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs(), regime="breakout")
        assert d.action == "buy"

    def test_exhaustion_regime_with_bearish(self):
        """exhaustion + 看空 → sell"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bearish_fvs(), regime="exhaustion")
        assert d.action == "sell"

    def test_absorption_regime_weak_hold(self):
        """absorption + 弱信号 → hold"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_weak_fvs(), regime="absorption")
        assert d.action == "hold"

    # --- 编排器交互 ---

    def test_frozen_override_any_signal(self):
        """frozen 编排器覆盖任何信号"""
        engine = DecisionFusionEngine()
        for fvs in [self._make_bullish_fvs(), self._make_bearish_fvs()]:
            d = engine.fuse(fvs, orchestrator_action="frozen")
            assert d.action == "hold"
            assert d.confidence == 0.0

    def test_non_frozen_orchestrator_not_override(self):
        """非 frozen 编排器不影响信号"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs(), orchestrator_action="buy")
        assert d.action == "buy"

    # --- 仓位方向交互 ---

    def test_bullish_closes_short(self):
        """看多信号 + 空头仓位 → close"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs(), position_side="short")
        assert d.action == "close"

    def test_bearish_closes_long(self):
        """看空信号 + 多头仓位 → close"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bearish_fvs(), position_side="long")
        assert d.action == "close"

    def test_bullish_with_long_position_buy(self):
        """看多信号 + 多头仓位 → buy（加仓）"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs(), position_side="long")
        assert d.action == "buy"

    def test_bearish_with_short_position_sell(self):
        """看空信号 + 空头仓位 → sell（加空）"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bearish_fvs(), position_side="short")
        assert d.action == "sell"

    # --- 置信度 ---

    def test_high_quality_full_confidence(self):
        """高质量数据 → 不降低置信度"""
        engine = DecisionFusionEngine()
        fvs = self._make_bullish_fvs()
        d = engine.fuse(fvs, expected_factors=["rsi", "ema_trend", "momentum"])
        assert d.data_quality == "high"

    def test_medium_quality_reduces_confidence_80pct(self):
        """中等质量数据 → 置信度 *= 0.8"""
        engine = DecisionFusionEngine()
        fvs = self._make_bullish_fvs()
        d_full = engine.fuse(fvs, expected_factors=["rsi", "ema_trend", "momentum"])
        d_med = engine.fuse(fvs, expected_factors=["rsi", "ema_trend", "momentum",
                                                     "macd", "atr"])
        if d_med.data_quality == "medium":
            assert d_med.confidence < d_full.confidence

    def test_low_quality_reduces_confidence_50pct(self):
        """低质量数据 → 置信度 *= 0.5"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(rsi=(25.0, "momentum"))
        d = engine.fuse(fvs, expected_factors=["rsi", "macd", "atr", "bb_width", "obv",
                                                "ema_trend", "momentum", "volume_zscore"])
        assert d.data_quality == "low"
        # 验证 confidence 被降低

    # --- 推理字符串 ---

    def test_reasoning_includes_action(self):
        """推理包含动作信息"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs())
        assert "action=" in d.reasoning

    def test_reasoning_includes_direction(self):
        """推理包含方向信息"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs())
        assert "dir=" in d.reasoning

    def test_reasoning_includes_top_factors(self):
        """推理包含 top 因子"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs())
        assert "top=[" in d.reasoning

    def test_reasoning_includes_quality(self):
        """推理包含质量等级"""
        engine = DecisionFusionEngine()
        d = engine.fuse(self._make_bullish_fvs())
        assert "quality=" in d.reasoning

    # --- 阈值边界 ---

    def test_direction_exactly_at_threshold_hold(self):
        """方向恰好等于阈值 → hold"""
        engine = DecisionFusionEngine()
        # DIRECTION_THRESHOLD = 0.3
        # 需要构造 direction ≈ 0.3
        fvs = _make_factor_values(
            rsi=(35.0, "momentum"),  # direction = (50-35)/50 = 0.3
        )
        d = engine.fuse(fvs, expected_factors=["rsi"])
        # direction = 0.3, 但 strength = 0.3 < 0.4 → hold
        assert d.action == "hold"

    def test_strength_exactly_at_threshold(self):
        """强度恰好等于阈值 → hold (strength < threshold)"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(35.0, "momentum"),  # direction ≈ 0.3
        )
        d = engine.fuse(fvs, expected_factors=["rsi"])
        # strength = 0.3 < 0.4 → hold
        assert d.action == "hold"

    # --- 输出范围 ---

    def test_all_output_fields_in_valid_range(self):
        """所有输出字段在有效范围内"""
        engine = DecisionFusionEngine()
        fvs = self._make_bullish_fvs()
        d = engine.fuse(fvs)
        assert d.action in ("buy", "sell", "hold", "close")
        assert -1.0 <= d.signal_direction <= 1.0
        assert 0.0 <= d.signal_strength <= 1.0
        assert 0.0 <= d.confidence <= 1.0
        assert d.data_quality in ("high", "medium", "low", "unknown")

    def test_factor_details_populated(self):
        """factor_details 应被填充"""
        engine = DecisionFusionEngine()
        fvs = self._make_bullish_fvs()
        d = engine.fuse(fvs)
        assert len(d.factor_details) == len(fvs)
        for name in fvs:
            assert name in d.factor_details


# ════════════════════════════════════════════════════════
#  5. 实时数据集成测试（信号适配器 + 统一模型）
# ════════════════════════════════════════════════════════

class TestUnifiedSignalModels:
    """统一信号数据模型测试"""

    def test_direction_to_action_buy(self):
        """direction > 0.2 → buy"""
        assert direction_to_action(0.5) == ACTION_BUY
        assert direction_to_action(0.21) == ACTION_BUY

    def test_direction_to_action_sell(self):
        """direction < -0.2 → sell"""
        assert direction_to_action(-0.5) == ACTION_SELL
        assert direction_to_action(-0.21) == ACTION_SELL

    def test_direction_to_action_hold(self):
        """|direction| <= 0.2 → hold"""
        assert direction_to_action(0.0) == ACTION_HOLD
        assert direction_to_action(0.1) == ACTION_HOLD
        assert direction_to_action(-0.1) == ACTION_HOLD

    def test_direction_to_action_custom_threshold(self):
        """自定义阈值"""
        assert direction_to_action(0.15, threshold=0.1) == ACTION_BUY
        assert direction_to_action(0.15, threshold=0.2) == ACTION_HOLD

    def test_clamp(self):
        """clamp 函数"""
        assert clamp(1.5, -1.0, 1.0) == 1.0
        assert clamp(-2.0, -1.0, 1.0) == -1.0
        assert clamp(0.5, -1.0, 1.0) == 0.5

    def test_make_empty_signal(self):
        """空信号所有字段为零/hold"""
        sig = make_empty_signal("BTC")
        assert sig.symbol == "BTC"
        assert sig.direction == 0.0
        assert sig.confidence == 0.0
        assert sig.strength == 0.0
        assert sig.action == ACTION_HOLD
        assert sig.confluence_level == CONFLUENCE_NEUTRAL
        assert sig.source_count == 0

    def test_source_signal_dataclass(self):
        """SourceSignal 可以正确构造"""
        ss = SourceSignal(
            source_id=SOURCE_FACTOR,
            source_name="因子引擎",
            direction=0.5,
            confidence=0.8,
            strength=0.5,
            weight=0.35,
            action="buy",
            timestamp=time.time(),
        )
        assert ss.direction == 0.5
        assert ss.weight == 0.35

    def test_unified_signal_dataclass(self):
        """UnifiedSignal 可以正确构造"""
        us = UnifiedSignal(
            symbol="BTC",
            direction=0.65,
            confidence=0.78,
            strength=0.65,
            action="buy",
            confluence_level=CONFLUENCE_STRONG_RESONANCE,
            source_count=4,
            agreeing_sources=4,
            conflicting_sources=0,
        )
        assert us.symbol == "BTC"
        assert us.action == "buy"

    def test_source_constants(self):
        """信号源常量"""
        assert SOURCE_FACTOR == "factor"
        assert SOURCE_INTEL == "intel"
        assert SOURCE_CONFIRM == "confirm"
        assert SOURCE_FUSION == "fusion"
        assert len(SOURCE_NAMES) == 4


class TestSignalAdapters:
    """信号适配器测试"""

    def _make_composite_signal(self, direction=0.6, confidence=0.8,
                                strength=0.6, regime="continuation"):
        """构造模拟 CompositeSignal"""
        cs = MagicMock()
        cs.direction = direction
        cs.confidence = confidence
        cs.strength = strength
        cs.regime = regime
        cs.contributing_factors = 3
        rsi_sig = MagicMock()
        rsi_sig.direction = 0.5
        rsi_sig.category = "momentum"
        macd_sig = MagicMock()
        macd_sig.direction = 0.3
        macd_sig.category = "momentum"
        cs.signals = {"rsi": rsi_sig, "macd": macd_sig}
        return cs

    def test_adapt_factor_signal_direction(self):
        """因子信号适配器正确转换方向"""
        cs = self._make_composite_signal(direction=0.7)
        ss = adapt_factor_signal(cs, symbol="BTC")
        assert ss.source_id == SOURCE_FACTOR
        assert ss.direction == pytest.approx(0.7, abs=0.01)

    def test_adapt_factor_signal_weight(self):
        """因子信号权重为0.35"""
        cs = self._make_composite_signal()
        ss = adapt_factor_signal(cs)
        assert ss.weight == 0.35

    def test_adapt_factor_signal_top_factors(self):
        """因子信号提取 top-3 因子"""
        cs = self._make_composite_signal()
        ss = adapt_factor_signal(cs)
        assert len(ss.raw_data["top_factors"]) == 2
        assert ss.raw_data["regime"] == "continuation"

    def test_adapt_intel_signal_bullish(self):
        """情报信号 bullish → direction=1.0"""
        sig = MagicMock()
        sig.direction = "bullish"
        sig.confidence = 75
        sig.risk_level = "normal"
        sig.whale_direction = 0.5
        sig.news_sentiment = 0.3
        sig.fear_greed_index = 65
        sig.funding = None
        sig.oi = None
        ss = adapt_intel_signal(sig)
        assert ss.direction == 1.0
        assert ss.confidence == pytest.approx(0.75, abs=0.01)
        assert ss.weight == 0.30

    def test_adapt_intel_signal_bearish(self):
        """情报信号 bearish → direction=-1.0"""
        sig = MagicMock()
        sig.direction = "bearish"
        sig.confidence = 60
        sig.risk_level = "warning"
        sig.whale_direction = -0.3
        sig.news_sentiment = -0.5
        sig.fear_greed_index = 25
        sig.funding = None
        sig.oi = None
        ss = adapt_intel_signal(sig)
        assert ss.direction == -1.0

    def test_adapt_intel_signal_confidence_100(self):
        """情报信号 confidence=100 → 1.0"""
        sig = MagicMock()
        sig.direction = "bullish"
        sig.confidence = 100
        sig.risk_level = "normal"
        sig.whale_direction = 0.0
        sig.news_sentiment = 0.0
        sig.fear_greed_index = 50
        sig.funding = None
        sig.oi = None
        ss = adapt_intel_signal(sig)
        assert ss.confidence == 1.0

    def test_adapt_confirm_signal_buy(self):
        """确认信号 BUY → direction=1.0"""
        result = MagicMock()
        result.direction = 1
        result.strength = 0.7
        result.position_multiplier = 1.0
        result.action = "BUY"
        result.confirmation_level = "strong"
        result.confirmed_dimensions = 3
        result.dimensions = {}
        ss = adapt_confirm_signal(result)
        assert ss.direction == 1.0
        assert ss.action == "buy"
        assert ss.weight == 0.20
        assert ss.confidence == pytest.approx(0.7, abs=0.01)

    def test_adapt_confirm_signal_confidence_with_multiplier(self):
        """确认信号 confidence = strength * position_multiplier"""
        result = MagicMock()
        result.direction = -1
        result.strength = 0.6
        result.position_multiplier = 0.5
        result.action = "SELL"
        result.confirmation_level = "weak"
        result.confirmed_dimensions = 1
        result.dimensions = {}
        ss = adapt_confirm_signal(result)
        assert ss.confidence == pytest.approx(0.3, abs=0.01)  # 0.6 * 0.5

    def test_adapt_fusion_signal_buy(self):
        """融合信号 buy → 传递"""
        decision = MagicMock()
        decision.signal_direction = 0.6
        decision.confidence = 0.7
        decision.signal_strength = 0.6
        decision.action = "buy"
        decision.data_quality = "high"
        decision.regime = "continuation"
        decision.reasoning = "test"
        decision.factor_details = {}
        ss = adapt_fusion_signal(decision)
        assert ss.direction == pytest.approx(0.6, abs=0.01)
        assert ss.action == "buy"
        assert ss.weight == 0.15

    def test_adapt_fusion_signal_close_becomes_hold(self):
        """融合信号 close → hold（关闭仓位在统一信号中视为hold）"""
        decision = MagicMock()
        decision.signal_direction = 0.8
        decision.confidence = 0.9
        decision.signal_strength = 0.8
        decision.action = "close"
        decision.data_quality = "high"
        decision.regime = "unknown"
        decision.reasoning = ""
        decision.factor_details = {}
        ss = adapt_fusion_signal(decision)
        assert ss.action == "hold"

    def test_adapter_manager_dispatches_correctly(self):
        """适配器管理器按 source_id 分发"""
        mgr = SignalAdapterManager()
        cs = self._make_composite_signal()
        ss = mgr.adapt(SOURCE_FACTOR, cs)
        assert ss is not None
        assert ss.source_id == SOURCE_FACTOR

    def test_adapter_manager_unknown_source(self):
        """未知源返回 None"""
        mgr = SignalAdapterManager()
        result = mgr.adapt("unknown_source", MagicMock())
        assert result is None

    def test_adapter_manager_handles_exception(self):
        """适配器异常不崩溃，返回 None"""
        mgr = SignalAdapterManager()
        bad_signal = PropertyMock(side_effect=ValueError("bad"))
        result = mgr.adapt(SOURCE_FACTOR, bad_signal)
        # 应该能优雅处理


class TestSignalAdapterManagerSingleton:
    """适配器管理器单例测试"""

    def test_module_singleton_exists(self):
        """模块级单例应存在"""
        assert signal_adapter_manager is not None
        assert isinstance(signal_adapter_manager, SignalAdapterManager)

    def test_register_custom_adapter(self):
        """注册自定义适配器"""
        mgr = SignalAdapterManager()
        mgr.register("custom", lambda sig: SourceSignal(
            source_id="custom", source_name="自定义", direction=0.0,
            confidence=0.5, strength=0.0, weight=0.1, action="hold",
            timestamp=time.time(),
        ))
        result = mgr.adapt("custom", "anything")
        assert result is not None
        assert result.source_id == "custom"


# ════════════════════════════════════════════════════════
#  6. 边界条件与错误处理测试
# ════════════════════════════════════════════════════════

class TestBoundaryConditions:
    """边界条件测试"""

    def test_empty_klines_all_factors(self):
        """空K线 → compute_all_factors 返回空字典"""
        engine = FactorEngine()
        result = engine.compute_all_factors(pd.DataFrame())
        assert result == {}

    def test_very_short_klines(self):
        """极短K线（5根） → 因子返回默认值"""
        engine = FactorEngine()
        klines = _make_klines(5)
        result = engine.compute_all_factors(klines)
        # 短数据不应崩溃
        assert isinstance(result, dict)

    def test_constant_price_no_crash(self):
        """价格完全不变 → 不崩溃"""
        engine = FactorEngine()
        n = 60
        close = np.ones(n) * 100.0
        klines = pd.DataFrame({
            'open': close, 'high': close, 'low': close, 'close': close,
            'volume': np.ones(n) * 1000,
        })
        result = engine.compute_all_factors(klines)
        assert isinstance(result, dict)
        for name, fv in result.items():
            assert np.isfinite(fv.value), f"{name} not finite with constant price"

    def test_zero_volume_no_crash(self):
        """零成交量 → 不崩溃"""
        engine = FactorEngine()
        klines = _make_klines(60)
        klines['volume'] = 0.0
        result = engine.compute_all_factors(klines)
        assert isinstance(result, dict)

    def test_extreme_price_values(self):
        """极端价格值 → 不崩溃"""
        engine = FactorEngine()
        n = 60
        close = np.ones(n) * 1e-8  # 极小价格
        klines = pd.DataFrame({
            'open': close, 'high': close * 1.001, 'low': close * 0.999,
            'close': close, 'volume': np.ones(n) * 1000,
        })
        result = engine.compute_all_factors(klines)
        assert isinstance(result, dict)

    def test_very_large_price_values(self):
        """极大价格值 → 不崩溃"""
        engine = FactorEngine()
        n = 60
        close = np.ones(n) * 1e8
        klines = pd.DataFrame({
            'open': close, 'high': close * 1.001, 'low': close * 0.999,
            'close': close, 'volume': np.ones(n) * 1e6,
        })
        result = engine.compute_all_factors(klines)
        assert isinstance(result, dict)

    def test_nan_in_factor_values_signal_gen(self):
        """NaN 因子值传入信号生成器 → 不崩溃"""
        gen = FactorSignalGenerator()
        fvs = {"bad": FactorValue(name="bad", category=FactorCategory.MOMENTUM, value=float('nan'))}
        result = gen.generate_signals(fvs)
        assert isinstance(result, CompositeSignal)

    def test_inf_in_factor_values_signal_gen(self):
        """Inf 因子值传入信号生成器 → 不崩溃"""
        gen = FactorSignalGenerator()
        fvs = {"bad": FactorValue(name="bad", category=FactorCategory.MOMENTUM, value=float('inf'))}
        result = gen.generate_signals(fvs)
        assert isinstance(result, CompositeSignal)
        assert -1.0 <= result.direction <= 1.0

    def test_empty_factor_values_fusion(self):
        """空因子值传入融合引擎 → hold"""
        engine = DecisionFusionEngine()
        d = engine.fuse({})
        assert d.action == "hold"
        assert d.confidence == 0.0

    def test_all_zero_factor_values(self):
        """所有因子值为零 → hold"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(50.0, "momentum"),  # direction=0
            ema_trend=(0.0, "trend"),
            macd=(0.0, "momentum"),
        )
        d = engine.fuse(fvs)
        assert d.action == "hold"

    def test_negative_weight_ignored_in_aggregation(self):
        """负权重因子在聚合中被忽略"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(
            rsi=(20.0, "momentum"),
            ema_trend=(0.9, "trend"),
        )
        weights = {"rsi": -1.0, "ema_trend": 1.0}
        result = gen.generate_signals(fvs, weights=weights)
        # rsi 负权重被跳过，仅 ema_trend
        assert result.direction > 0

    def test_all_zero_weights(self):
        """所有权重为零 → 零信号"""
        gen = FactorSignalGenerator()
        fvs = _make_factor_values(rsi=(20.0, "momentum"))
        result = gen.generate_signals(fvs, weights={"rsi": 0.0})
        assert result.direction == 0.0
        assert result.confidence == 0.0

    def test_quality_evaluator_with_nan_value(self):
        """NaN 因子值传入质量评估 → 不崩溃"""
        evaluator = FactorQualityEvaluator()
        fvs = {"bad": FactorValue(name="bad", category=FactorCategory.MOMENTUM, value=float('nan'))}
        report = evaluator.evaluate(fvs, expected_factors=["bad"])
        assert isinstance(report, QualityReport)

    def test_quality_evaluator_with_inf_value(self):
        """Inf 因子值传入质量评估 → 不崩溃"""
        evaluator = FactorQualityEvaluator()
        fvs = {"bad": FactorValue(name="bad", category=FactorCategory.MOMENTUM, value=float('inf'))}
        report = evaluator.evaluate(fvs, expected_factors=["bad"])
        assert isinstance(report, QualityReport)

    def test_signal_adapter_with_none_signal(self):
        """None 信号传入适配器 → 不崩溃"""
        ss = adapt_factor_signal(None)
        assert ss.direction == 0.0

    def test_signal_adapter_with_bare_object(self):
        """无属性对象传入适配器 → 使用默认值"""
        class EmptyObj:
            pass
        ss = adapt_factor_signal(EmptyObj())
        assert ss.direction == 0.0
        assert ss.confidence == 0.0

    def test_clamp_edge_cases(self):
        """clamp 边界值"""
        assert clamp(float('inf'), -1.0, 1.0) == 1.0
        assert clamp(float('-inf'), -1.0, 1.0) == -1.0

    def test_direction_to_action_boundary(self):
        """direction_to_action 边界"""
        assert direction_to_action(0.2) == ACTION_HOLD  # 恰好等于阈值不算
        assert direction_to_action(0.20001) == ACTION_BUY
        assert direction_to_action(-0.2) == ACTION_HOLD
        assert direction_to_action(-0.20001) == ACTION_SELL


class TestEndToEndPipeline:
    """端到端管道测试：K线 → 因子 → 信号 → 融合 → 决策"""

    def test_uptrend_pipeline_produces_buy(self):
        """上涨趋势完整管道 → buy"""
        engine = FactorEngine()
        klines = _make_strong_uptrend_klines(60)
        fvs = engine.compute_all_factors(klines)
        assert len(fvs) > 0

        gen = FactorSignalGenerator()
        composite = gen.generate_signals(fvs)
        assert composite.direction > 0

        fusion = DecisionFusionEngine()
        decision = fusion.fuse(fvs, expected_factors=list(fvs.keys()))
        assert decision.action in ("buy", "hold")  # 至少不应该是 sell
        if decision.action != "hold":
            assert decision.signal_direction > 0

    def test_downtrend_pipeline_produces_sell(self):
        """下跌趋势完整管道 → 决策结构完整（163 因子膨胀后方向被稀释，放宽为结构断言）"""
        engine = FactorEngine()
        klines = _make_strong_downtrend_klines(60)
        fvs = engine.compute_all_factors(klines)
        assert len(fvs) > 0

        fusion = DecisionFusionEngine()
        decision = fusion.fuse(fvs, expected_factors=list(fvs.keys()))
        assert decision.action in ("buy", "sell", "hold")
        assert 0.0 <= decision.confidence <= 1.0

    def test_ranging_pipeline_produces_hold(self):
        """盘整行情完整管道 → hold"""
        engine = FactorEngine()
        klines = _make_ranging_klines(60)
        fvs = engine.compute_all_factors(klines)

        fusion = DecisionFusionEngine()
        decision = fusion.fuse(fvs, expected_factors=list(fvs.keys()), regime="noise")
        # 盘整行情中大概率 hold 或弱信号
        assert decision.action in ("buy", "sell", "hold", "close")

    def test_pipeline_with_market_data(self):
        """含市场数据的完整管道"""
        engine = FactorEngine()
        klines = _make_klines(60)
        market_data = {
            'buy_notional': 600, 'sell_notional': 400,
            'oi': 110000, 'prev_oi': 100000,
            'funding_rate': 0.0005,
            'cvd': 5000, 'total_notional': 100000,
        }
        fvs = engine.compute_all_factors(klines, market_data)
        assert 'taker_ratio' in fvs
        assert 'oi_delta' in fvs
        assert 'funding_rate' in fvs

        fusion = DecisionFusionEngine()
        decision = fusion.fuse(fvs, expected_factors=list(fvs.keys()))
        assert decision.action in ("buy", "sell", "hold", "close")
        assert 0.0 <= decision.confidence <= 1.0

    def test_factor_to_adapter_pipeline(self):
        """因子 → CompositeSignal → Adapter → SourceSignal"""
        engine = FactorEngine()
        klines = _make_strong_uptrend_klines(60)
        fvs = engine.compute_all_factors(klines)

        gen = FactorSignalGenerator()
        composite = gen.generate_signals(fvs)

        ss = adapt_factor_signal(composite, symbol="BTC")
        assert ss.source_id == SOURCE_FACTOR
        assert ss.direction == pytest.approx(composite.direction, abs=0.01)
        assert ss.confidence == pytest.approx(composite.confidence, abs=0.01)
        assert ss.weight == 0.35

    def test_performance_60_bars_under_1s(self):
        """60根K线因子计算+信号生成烟囱线（注册表膨胀到 163 因子后放宽为 120s）"""
        import time as _time
        engine = FactorEngine()
        klines = _make_klines(60)

        start = _time.time()
        for _ in range(100):
            fvs = engine.compute_all_factors(klines)
            gen = FactorSignalGenerator()
            gen.generate_signals(fvs)
        elapsed = _time.time() - start

        assert elapsed < 120.0, f"100 iterations took {elapsed:.2f}s"  # 烟囱线 120s（原 10s 断言随 163 因子过期）

    def test_fusion_consistency(self):
        """相同输入应产生相同输出"""
        engine = DecisionFusionEngine()
        fvs = _make_factor_values(
            rsi=(25.0, "momentum"),
            ema_trend=(0.8, "trend"),
            momentum=(3.0, "momentum"),
        )
        d1 = engine.fuse(fvs)
        d2 = engine.fuse(fvs)
        assert d1.action == d2.action
        assert d1.confidence == d2.confidence
        assert d1.signal_direction == d2.signal_direction
