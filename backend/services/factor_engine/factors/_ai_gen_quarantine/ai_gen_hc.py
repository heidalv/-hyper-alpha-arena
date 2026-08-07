"""AI因子: 高位横盘因子 | 置信:60% | 检测价格是否在近期高位附近窄幅震荡。计算最新收盘价在近N日价格区间内的百分位，并结合区间宽度与ATR的比值。若价格处于高位且区间窄，则输出负值，表示多头风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HighLevelConsolidation(BaseFactor):
    """检测价格是否在近期高位附近窄幅震荡。计算最新收盘价在近N日价格区间内的百分位，并结合区间宽度与ATR的比值。若价格处于高位且区间窄，则输出负值，表示多头风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hc",
            name="High-level Consolidation",
            display_name="高位横盘因子",
            description="检测价格是否在近期高位附近窄幅震荡。计算最新收盘价在近N日价格区间内的百分位，并结合区间宽度与ATR的比值。若价格处于高位且区间窄，则输出负值，表示多头风险。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算近20日最高价和最低价
        high = data['high']
        low = data['low']
        close = data['close']
        lookback = 20
        recent_high = high.rolling(lookback).max()
        recent_low = low.rolling(lookback).min()
        # 价格在区间内的位置 (0~1)
        price_range = recent_high - recent_low
        price_pos = (close - recent_low) / (price_range + 1e-10)
        # 计算区间宽度与ATR的比值，衡量震荡程度
        tr = pd.concat([high - low,
                        abs(high - close.shift(1)),
                        abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        width_ratio = price_range / (atr + 1e-10)
        # 高位且区间窄：price_pos > 0.8 且 width_ratio < 2 时风险高
        high_risk = (price_pos > 0.8).astype(float) * (width_ratio < 2).astype(float)
        # 结合位置：高位越接近1，风险越大，同时考虑宽度比
        combined = price_pos * (1 - width_ratio / 3).clip(0, 1)
        # 映射到[-1,0]范围内
        factor = -combined
        factor = factor.fillna(0)
        return factor
