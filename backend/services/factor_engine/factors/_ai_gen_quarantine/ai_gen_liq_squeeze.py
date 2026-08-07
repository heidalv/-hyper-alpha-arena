"""AI因子: 流动性挤压因子 | 置信:60% | 当价格处于近期区间中部且成交量显著萎缩时，流动性不足，容易导致持仓超时或反转。因子值负向表示流动性挤压风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquiditySqueezeFactor(BaseFactor):
    """当价格处于近期区间中部且成交量显著萎缩时，流动性不足，容易导致持仓超时或反转。因子值负向表示流动性挤压风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_squeeze",
            name="Liquidity Squeeze Factor",
            display_name="流动性挤压因子",
            description="当价格处于近期区间中部且成交量显著萎缩时，流动性不足，容易导致持仓超时或反转。因子值负向表示流动性挤压风险高。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 近期区间（20期）内的价格位置
        period = 20
        high_high = data['high'].rolling(period).max()
        low_low = data['low'].rolling(period).min()
        price_pos = (data['close'] - low_low) / (high_high - low_low + 1e-10)
        # 成交量相对20日均值的萎缩程度
        vol = data['volume']
        vol_ma = vol.rolling(period).mean()
        vol_ratio = vol / (vol_ma + 1e-10)
        # 综合：价格居中（0.3-0.7）且成交量萎缩（vol_ratio<0.8）时信号为负
        cond = (price_pos.between(0.3, 0.7)) & (vol_ratio < 0.8)
        result = pd.Series(index=data.index, dtype=float)
        result[cond] = -1.0
        result[~cond] = 1.0
        return result.fillna(0)
