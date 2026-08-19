"""AI因子: 波动率压缩爆发 | 置信:60% | 布林带宽度收缩到极低水平后出现价格突破并伴随成交量激增，预示单边行情启动。使用带宽百分比（带宽/中轨）低于10%且成交量放大2倍作为触发，随后方向由突破方向决定。返回正值看涨，负值看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueezeBurst(BaseFactor):
    """布林带宽度收缩到极低水平后出现价格突破并伴随成交量激增，预示单边行情启动。使用带宽百分比（带宽/中轨）低于10%且成交量放大2倍作为触发，随后方向由突破方向决定。返回正值看涨，负值看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_squeeze",
            name="Volatility Squeeze Burst",
            display_name="波动率压缩爆发",
            description="布林带宽度收缩到极低水平后出现价格突破并伴随成交量激增，预示单边行情启动。使用带宽百分比（带宽/中轨）低于10%且成交量放大2倍作为触发，随后方向由突破方向决定。返回正值看涨，负值看空。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 计算布林带
        period = 20
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        band_width = (upper - lower) / sma
        # 带宽低于10%为压缩条件
        squeeze = (band_width < 0.10).astype(int)
        # 当前收盘价突破上轨或下轨
        above_upper = (close > upper).astype(int)
        below_lower = (close < lower).astype(int)
        # 成交量放大2倍相对20日均量
        vol_ma = volume.rolling(period).mean()
        vol_surge = (volume > vol_ma * 2).astype(int)
        # 综合信号：压缩且突破且放量
        long_signal = squeeze * above_upper * vol_surge
        short_signal = squeeze * below_lower * vol_surge
        # 映射到[-1,1]
        result = long_signal.astype(float) - short_signal.astype(float)
        # 平滑处理
        result = result.rolling(3).mean().fillna(0)
        result = np.clip(result, -1, 1)
        return result
