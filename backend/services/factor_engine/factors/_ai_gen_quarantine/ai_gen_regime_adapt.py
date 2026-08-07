"""AI因子: 自适应市场状态动量 | 置信:70% | 根据近期波动率和趋势持续性动态调整动量信号，避免在未知市场状态（高噪声）下产生虚假信号。当因子接近+1表示强趋势向上，-1表示强趋势向下，0附近表示震荡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeAdaptiveMomentum(BaseFactor):
    """根据近期波动率和趋势持续性动态调整动量信号，避免在未知市场状态（高噪声）下产生虚假信号。当因子接近+1表示强趋势向上，-1表示强趋势向下，0附近表示震荡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_adapt",
            name="regime_adaptive_momentum",
            display_name="自适应市场状态动量",
            description="根据近期波动率和趋势持续性动态调整动量信号，避免在未知市场状态（高噪声）下产生虚假信号。当因子接近+1表示强趋势向上，-1表示强趋势向下，0附近表示震荡。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算短期和长期动量
        short_ret = data['close'].pct_change(5)
        long_ret = data['close'].pct_change(20)
        # 波动率调整因子 (过去10日ATR/价格)
        tr = pd.concat([data['high'] - data['low'],
                        (data['high'] - data['close'].shift()).abs(),
                        (data['low'] - data['close'].shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(10).mean()
        vol_adj = atr / data['close']
        # 市场状态：当波动率低且趋势一致时放大动量，高波动时缩小
        vol_regime = 1 / (1 + (vol_adj / vol_adj.rolling(50).mean()).clip(0.5, 5))
        # 动量得分
        momentum = (short_ret * 0.4 + long_ret * 0.6) * vol_regime
        # 归一化到[-1,1]
        result = momentum / (momentum.abs().rolling(20).mean() + 1e-10)
        result = result.clip(-1, 1)
        return result
