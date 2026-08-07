"""AI因子: 趋势衰减动量因子 | 置信:55% | 使用ADX和价格动量识别趋势衰竭。当ADX(14)从高位回落（当前低于前一日且大于25）且价格接近过去20日极值（最高价或最低价）时，反向开仓。若价格接近最高且ADX下降，做空(-1)；接近最低且ADX下降，做多(+1)。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendFadeMomentum(BaseFactor):
    """使用ADX和价格动量识别趋势衰竭。当ADX(14)从高位回落（当前低于前一日且大于25）且价格接近过去20日极值（最高价或最低价）时，反向开仓。若价格接近最高且ADX下降，做空(-1)；接近最低且ADX下降，做多(+1)。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendfade",
            name="Trend Fade Momentum",
            display_name="趋势衰减动量因子",
            description="使用ADX和价格动量识别趋势衰竭。当ADX(14)从高位回落（当前低于前一日且大于25）且价格接近过去20日极值（最高价或最低价）时，反向开仓。若价格接近最高且ADX下降，做空(-1)；接近最低且ADX下降，做多(+1)。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 计算ADX
        n = 14
        high, low, close = data['high'], data['low'], data['close']
        # 方向运动
        up = high - high.shift(1)
        down = low.shift(1) - low
        plus_dm = np.where((up > down) & (up > 0), up, 0)
        minus_dm = np.where((down > up) & (down > 0), down, 0)
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(n).mean()
        plus_di = 100 * pd.Series(plus_dm, index=data.index).rolling(n).sum() / (atr * n)
        minus_di = 100 * pd.Series(minus_dm, index=data.index).rolling(n).sum() / (atr * n)
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(n).mean()
        # ADX下降：当前小于前一日
        adx_decline = adx < adx.shift(1)
        # 价格接近20日极值
        roll_max = high.rolling(20).max()
        roll_min = low.rolling(20).min()
        near_high = close >= roll_max * 0.98
        near_low = close <= roll_min * 1.02
        # ADX大于25（有效趋势）
        strong_trend = adx > 25
        # 信号
        condition_short = strong_trend & adx_decline & near_high
        condition_long = strong_trend & adx_decline & near_low
        result = pd.Series(0, index=data.index)
        result[condition_long] = 1
        result[condition_short] = -1
        return result
