"""AI因子: 未知市场状态波动钳制因子 | 置信:50% | 针对regime=unknown的普遍亏损，当市场状态不明确时（表现为趋势指标与波动率背离），通过压缩信号强度来规避风险。用短期动量与长期波动率的比值，在波动率异常升高时反向抑制信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownVolatilityClamp(BaseFactor):
    """针对regime=unknown的普遍亏损，当市场状态不明确时（表现为趋势指标与波动率背离），通过压缩信号强度来规避风险。用短期动量与长期波动率的比值，在波动率异常升高时反向抑制信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rgunk",
            name="Regime_Unknown_Volatility_Clamp",
            display_name="未知市场状态波动钳制因子",
            description="针对regime=unknown的普遍亏损，当市场状态不明确时（表现为趋势指标与波动率背离），通过压缩信号强度来规避风险。用短期动量与长期波动率的比值，在波动率异常升高时反向抑制信号。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(3)
        long_vol = data['close'].pct_change(30).std()
        vol_ma = data['close'].pct_change().rolling(20).std()
        vol_ratio = long_vol / (vol_ma + 1e-9)
        result = (short_ret * (1 - vol_ratio)).clip(-1, 1)
        return result
