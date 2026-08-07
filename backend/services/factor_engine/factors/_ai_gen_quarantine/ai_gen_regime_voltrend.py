"""AI因子: 市场状态波动趋势因子 | 置信:65% | 通过短期收益率与波动率（ATR/价格）的比值来识别市场状态。比值极端时（过小或过大）标记为unknown状态，此时因子输出接近0；否则根据趋势方向给出信号。旨在避免在不明状态下进行方向性交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Volatility_Trend_Factor(BaseFactor):
    """通过短期收益率与波动率（ATR/价格）的比值来识别市场状态。比值极端时（过小或过大）标记为unknown状态，此时因子输出接近0；否则根据趋势方向给出信号。旨在避免在不明状态下进行方向性交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_voltrend",
            name="Regime Volatility Trend Factor",
            display_name="市场状态波动趋势因子",
            description="通过短期收益率与波动率（ATR/价格）的比值来识别市场状态。比值极端时（过小或过大）标记为unknown状态，此时因子输出接近0；否则根据趋势方向给出信号。旨在避免在不明状态下进行方向性交易。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算收益率
        ret = data['close'].pct_change()
        # ATR
        tr = pd.concat([data['high'] - data['low'],
                        (data['high'] - data['close'].shift(1)).abs(),
                        (data['low'] - data['close'].shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 归一化波动率
        norm_vol = atr / data['close']
        # 短期趋势强度：10日收益率绝对值
        trend_strength = ret.rolling(10).mean().abs()
        # 波动率与趋势比值
        ratio = trend_strength / (norm_vol + 1e-10)
        # 识别极端区域（基于滚动分位数）
        low = ratio.rolling(60).quantile(0.1)
        high = ratio.rolling(60).quantile(0.9)
        # unknown状态：比值在低分位或高分位之外
        unknown = (ratio < low) | (ratio > high)
        # 趋势方向
        direction = np.sign(ret.rolling(10).mean())
        # 最终因子：unknown时归零，否则给方向
        factor = direction * (~unknown).astype(float)
        return factor.fillna(0)
