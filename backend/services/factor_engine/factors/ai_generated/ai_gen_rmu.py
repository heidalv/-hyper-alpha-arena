"""AI因子: 未知行情动量过滤因子 | 置信:60% | 针对regime=unknown的普遍亏损：市场状态不明时，纯趋势或纯反转都易失效。此因子结合短期动量与长期均值偏离，若短期动量与长期偏离方向一致（顺势）则给予较小信号，若背离（逆势）则给予较大反向信号，避免在无趋势中追单。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownMomentumFilter(BaseFactor):
    """针对regime=unknown的普遍亏损：市场状态不明时，纯趋势或纯反转都易失效。此因子结合短期动量与长期均值偏离，若短期动量与长期偏离方向一致（顺势）则给予较小信号，若背离（逆势）则给予较大反向信号，避免在无趋势中追单。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rmu",
            name="Regime Unknown Momentum Filter",
            display_name="未知行情动量过滤因子",
            description="针对regime=unknown的普遍亏损：市场状态不明时，纯趋势或纯反转都易失效。此因子结合短期动量与长期均值偏离，若短期动量与长期偏离方向一致（顺势）则给予较小信号，若背离（逆势）则给予较大反向信号，避免在无趋势中追单。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(5)
        long_ret = data['close'].pct_change(30)
        long_ma = data['close'].rolling(30).mean()
        deviation = (data['close'] - long_ma) / (long_ma + 1e-9)
        momentum_aligned = short_ret * long_ret
        result = (-momentum_aligned * deviation).clip(-1, 1)
        return result
