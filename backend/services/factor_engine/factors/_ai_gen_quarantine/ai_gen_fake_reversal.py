"""AI因子: 虚假反转识别 | 置信:60% | 识别短期价格反转失败的模式，类似于亏损中的liq_magnet_reversal和ai_reverse。通过检测价格在快速下跌后出现微弱反弹但成交量萎缩、随后再次转向，判断反转是否为陷阱。值接近+1表示真实反转，-1表示虚假反转（应避免）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FakeReversalDetector(BaseFactor):
    """识别短期价格反转失败的模式，类似于亏损中的liq_magnet_reversal和ai_reverse。通过检测价格在快速下跌后出现微弱反弹但成交量萎缩、随后再次转向，判断反转是否为陷阱。值接近+1表示真实反转，-1表示虚假反转（应避免）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fake_reversal",
            name="Fake Reversal Detector",
            display_name="虚假反转识别",
            description="识别短期价格反转失败的模式，类似于亏损中的liq_magnet_reversal和ai_reverse。通过检测价格在快速下跌后出现微弱反弹但成交量萎缩、随后再次转向，判断反转是否为陷阱。值接近+1表示真实反转，-1表示虚假反转（应避免）。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
    
        # 短期动量（3周期）
        ret3 = close.pct_change(3)
        # 最近一段反向运动（假设之前下跌，现反弹）
        down_trend = ret3 < -0.02  # 过去3周期下跌超过2%
    
        # 反弹确认：当前价格高于前1日低点，但涨幅不大
        bounce = close > close.shift(1)  # 今日上涨
    
        # 反弹强度：成交量萎缩或价格冲高回落
        vol_ma5 = volume.rolling(5).mean()
        vol_shrink = volume < vol_ma5 * 0.8
    
        # 上影线长度
        upper_shadow = high - close
        shadow_ratio = upper_shadow / (high - low + 1e-8)
        long_upper = shadow_ratio > 0.6
    
        # 虚假反转条件：前期下跌，今日反弹但成交量萎缩且上影线长
        fake = down_trend & bounce & vol_shrink & long_upper
        # 真实反转条件：前期下跌，今日反弹且放量且无上影线
        real = down_trend & bounce & (volume > vol_ma5 * 1.2) & (shadow_ratio < 0.2)
    
        result = pd.Series(np.where(fake, -1.0, np.where(real, 1.0, 0.0)), index=data.index)
        return result
