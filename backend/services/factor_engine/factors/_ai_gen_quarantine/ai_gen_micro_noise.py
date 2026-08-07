"""AI因子: 微观噪声暴露 | 置信:60% | 捕捉微小价格波动和成交量异常导致的策略干扰（如dust_cleanup、reverse_netting）。通过计算价格变动的最小单位频率（tick级别噪声）和成交量的异常微小变化，当价格在极窄区间内频繁跳动且成交量不规则时，因子值接近0或负值，表示持仓风险。使用多日平均的真实波幅与价格之比，结合成交量变异系数。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MicroNoiseExposure(BaseFactor):
    """捕捉微小价格波动和成交量异常导致的策略干扰（如dust_cleanup、reverse_netting）。通过计算价格变动的最小单位频率（tick级别噪声）和成交量的异常微小变化，当价格在极窄区间内频繁跳动且成交量不规则时，因子值接近0或负值，表示持仓风险。使用多日平均的真实波幅与价格之比，结合成交量变异系数。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_micro_noise",
            name="Micro Noise Exposure",
            display_name="微观噪声暴露",
            description="捕捉微小价格波动和成交量异常导致的策略干扰（如dust_cleanup、reverse_netting）。通过计算价格变动的最小单位频率（tick级别噪声）和成交量的异常微小变化，当价格在极窄区间内频繁跳动且成交量不规则时，因子值接近0或负值，表示持仓风险。使用多日平均的真实波幅与价格之比，结合成交量变异系数。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
    
        # 真实波幅 (ATR) 归一化
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=1).mean()
        norm_tr = atr / close  # 相对波幅
    
        # 成交量变异系数 (CV)
        vol_std = volume.rolling(10, min_periods=1).std()
        vol_mean = volume.rolling(10, min_periods=1).mean()
        vol_cv = vol_std / (vol_mean + 1e-10)
    
        # 噪声水平：低波幅 + 高成交量CV -> 噪声高 (负值)
        noise = - ( (1 - norm_tr) * vol_cv )
        # 使用对称 sigmoid 压缩到 [-1,1]
        result = 2 / (1 + np.exp(-noise)) - 1
        return result.fillna(0.0)
