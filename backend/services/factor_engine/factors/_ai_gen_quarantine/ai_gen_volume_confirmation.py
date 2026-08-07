"""AI因子: 成交量不确认信号 | 置信:60% | 识别价格突破或反转时缺乏成交量支持的虚假运动。计算当前价格变化与成交量变化的相关系数或比值，当价格波动但成交量显著低于近期均值时，认为动能不可持续。因子输出负值表示假突破/假反转概率高，容易触发止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeNonConfirmationSignal(BaseFactor):
    """识别价格突破或反转时缺乏成交量支持的虚假运动。计算当前价格变化与成交量变化的相关系数或比值，当价格波动但成交量显著低于近期均值时，认为动能不可持续。因子输出负值表示假突破/假反转概率高，容易触发止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_confirmation",
            name="Volume Non-Confirmation Signal",
            display_name="成交量不确认信号",
            description="识别价格突破或反转时缺乏成交量支持的虚假运动。计算当前价格变化与成交量变化的相关系数或比值，当价格波动但成交量显著低于近期均值时，认为动能不可持续。因子输出负值表示假突破/假反转概率高，容易触发止损。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变化绝对值
        price_change = close.pct_change().abs()
        # 成交量相对均值比率
        vol_ma = volume.rolling(10).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 当价格波动大但成交量缩小时信号为负
        # 定义价格波动阈值: 最近5日价格变化标准差
        price_std = close.pct_change().rolling(5).std()
        high_volatility = price_change > price_std
        low_volume = vol_ratio < 0.8
        signal = -1.0 * (high_volatility & low_volume).astype(float)
        # 滚动平均平滑
        result = signal.rolling(3).mean().fillna(0)
        return result.clip(-1, 1)
