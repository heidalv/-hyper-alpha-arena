"""AI因子: 窄止损陷阱 | 置信:60% | 识别市场在极窄价格区间内反复震荡、突然放量突破后立即反转的模式，这恰好对应'regime=unknown'下的小止损单被扫损亏损。通过计算15分钟真实波幅与60分钟真实波幅的比值，并结合成交量相对于最近5分钟平均成交量的突变，当比值小于1且成交量飙升时发出反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Tiny Stop Loss Risk(BaseFactor):
    """识别市场在极窄价格区间内反复震荡、突然放量突破后立即反转的模式，这恰好对应'regime=unknown'下的小止损单被扫损亏损。通过计算15分钟真实波幅与60分钟真实波幅的比值，并结合成交量相对于最近5分钟平均成交量的突变，当比值小于1且成交量飙升时发出反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tinystop",
            name="Tiny Stop Loss Risk",
            display_name="窄止损陷阱",
            description="识别市场在极窄价格区间内反复震荡、突然放量突破后立即反转的模式，这恰好对应'regime=unknown'下的小止损单被扫损亏损。通过计算15分钟真实波幅与60分钟真实波幅的比值，并结合成交量相对于最近5分钟平均成交量的突变，当比值小于1且成交量飙升时发出反向信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            high = data['high']
            low = data['low']
            close = data['close']
            volume = data['volume']
    
            # 真实波幅 ATR
            tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
            atr_15 = tr.rolling(15).mean()
            atr_60 = tr.rolling(60).mean()
            # 波幅收缩比
            vol_shrink = atr_15 / atr_60.replace(0, 1e-10)
    
            # 成交量突变：当前成交量 / 最近5分钟平均成交量
            vol_surge = volume / volume.rolling(5).mean().replace(0, 1e-10)
    
            # 信号：当波幅收缩到0.8以下且成交量暴增至2倍以上 -> 可能陷阱
            condition = (vol_shrink < 0.8) & (vol_surge > 2.0)
            # 方向：突破方向的反向（用价格变化方向判断）
            price_change = close.pct_change(1).fillna(0)
            signal = np.where(condition, -np.sign(price_change), 0.0)
            result = pd.Series(signal, index=data.index)
            return result.fillna(0.0)
