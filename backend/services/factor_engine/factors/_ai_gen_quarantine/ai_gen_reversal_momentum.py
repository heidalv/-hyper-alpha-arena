"""AI因子: 反转动量捕捉 | 置信:60% | 基于短期动量(3周期)与长期动量(13周期)的背离，结合成交量异常放大，捕捉类似ai_reverse和reverse_netting的亏损模式。当短期动量与长期动量方向相反且成交量激增时，预示反转。输出[-1,1]，正值表示短期超卖后看涨反转，负值表示短期超买后看跌反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalMomentumCapture(BaseFactor):
    """基于短期动量(3周期)与长期动量(13周期)的背离，结合成交量异常放大，捕捉类似ai_reverse和reverse_netting的亏损模式。当短期动量与长期动量方向相反且成交量激增时，预示反转。输出[-1,1]，正值表示短期超卖后看涨反转，负值表示短期超买后看跌反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_momentum",
            name="Reversal Momentum Capture",
            display_name="反转动量捕捉",
            description="基于短期动量(3周期)与长期动量(13周期)的背离，结合成交量异常放大，捕捉类似ai_reverse和reverse_netting的亏损模式。当短期动量与长期动量方向相反且成交量激增时，预示反转。输出[-1,1]，正值表示短期超卖后看涨反转，负值表示短期超买后看跌反转。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
    
        # 计算短期动量（3周期ROC）
        roc_short = close.pct_change(3)
        # 计算长期动量（13周期ROC）
        roc_long = close.pct_change(13)
    
        # 计算成交量相对20日均值的倍数
        vol_ma = volume.rolling(20).mean()
        vol_spike = volume / vol_ma
        # 成交量激增条件：> 1.5倍均值
        vol_surge = (vol_spike > 1.5).astype(float)
    
        # 动量背离：短期与长期方向相反且绝对值都大于阈值(0.02表示2%)
        short_up = (roc_short > 0.02).astype(float)
        short_down = (roc_short < -0.02).astype(float)
        long_up = (roc_long > 0.02).astype(float)
        long_down = (roc_long < -0.02).astype(float)
    
        # 背离信号：短期看涨但长期看跌 => 短期超买反转向下 => 负信号
        bearish_div = (short_up & long_down).astype(float) * (-1)
        # 短期看跌但长期看涨 => 短期超卖反转向上 => 正信号
        bullish_div = (short_down & long_up).astype(float) * 1
    
        # 综合信号，考虑成交量激增增强
        signal = (bullish_div + bearish_div) * vol_surge
        # 平滑并限制
        result = signal.clip(-1, 1)
        return result
