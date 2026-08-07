"""AI因子: 弱势趋势ADX | 置信:60% | 使用ADX指标判断趋势强度，当ADX低于25时市场处于无趋势或震荡状态，在震荡市中做多容易亏损。输出负值表示趋势弱宜做空或观望。ADX计算采用标准14周期，归一化后映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Weak_Trend_ADX(BaseFactor):
    """使用ADX指标判断趋势强度，当ADX低于25时市场处于无趋势或震荡状态，在震荡市中做多容易亏损。输出负值表示趋势弱宜做空或观望。ADX计算采用标准14周期，归一化后映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adx_weak",
            name="Weak Trend ADX",
            display_name="弱势趋势ADX",
            description="使用ADX指标判断趋势强度，当ADX低于25时市场处于无趋势或震荡状态，在震荡市中做多容易亏损。输出负值表示趋势弱宜做空或观望。ADX计算采用标准14周期，归一化后映射到[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ADX (14周期)
        period = 14
        high = data['high']
        low = data['low']
        close = data['close']
        # TR
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        # +DM
        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 平滑
        tr_smooth = tr.rolling(period).mean()
        plus_di = 100 * plus_dm.rolling(period).mean() / tr_smooth
        minus_di = 100 * minus_dm.rolling(period).mean() / tr_smooth
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(period).mean()
        # 映射到[-1,1]: 当ADX<25时信号为负，且越低越负，反之正。用阈值映射
        # 使用sigmoid反转：1/(1+exp(-(adx-25))) 范围0-1，然后映射到[-1,1]：2*sigmoid-1 但方向需为负？
        # 我们希望ADX小为负，大为正，所以用2*sigmoid(adx-30)-1? 更简单：直接用 (adx-25)/25 截断到[-1,1]
        result = (adx - 25) / 25
        result = np.clip(result, -1, 1)
        result = result.ffill().fillna(0)
        return result
