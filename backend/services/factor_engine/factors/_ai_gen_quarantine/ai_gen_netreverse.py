"""AI因子: 反向净额反转 | 置信:55% | 检测价格与成交量背离：当价格创新低但成交量萎缩（空方衰竭），或价格创新高但成交量萎缩（多方衰竭），预示反转。使用最近5根K线价格高低点与成交量中位数对比，计算背离强度并映射。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NetOrderReversal(BaseFactor):
    """检测价格与成交量背离：当价格创新低但成交量萎缩（空方衰竭），或价格创新高但成交量萎缩（多方衰竭），预示反转。使用最近5根K线价格高低点与成交量中位数对比，计算背离强度并映射。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_netreverse",
            name="Net Order Reversal",
            display_name="反向净额反转",
            description="检测价格与成交量背离：当价格创新低但成交量萎缩（空方衰竭），或价格创新高但成交量萎缩（多方衰竭），预示反转。使用最近5根K线价格高低点与成交量中位数对比，计算背离强度并映射。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 近期高点、低点
        high_5 = data['high'].rolling(5).max()
        low_5 = data['low'].rolling(5).min()
        # 当前价格位置
        pos = (data['close'] - low_5) / (high_5 - low_5 + 1e-8)
        # 成交量中位数
        vol_med = data['volume'].rolling(5).median()
        vol_ratio = data['volume'] / (vol_med + 1e-8)
        # 背离：价格极低但成交量很低（<0.8）→ 看涨信号
        # 价格极高但成交量很低 → 看跌信号
        # 计算综合信号
        bearish = np.where((pos > 0.9) & (vol_ratio < 0.8), -1.0, 0.0)
        bullish = np.where((pos < 0.1) & (vol_ratio < 0.8), 1.0, 0.0)
        result = pd.Series(bearish + bullish, index=data.index).rolling(2).mean().fillna(0)
        return result
