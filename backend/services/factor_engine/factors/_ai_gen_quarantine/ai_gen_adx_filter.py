"""AI因子: 趋势强度过滤 | 置信:80% | 使用ADX指标判断趋势强弱，当ADX低于阈值（如25）时视为无趋势震荡状态，易导致频繁止损和持仓超时。因子值在ADX低时接近-1，高时接近1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Strength_Filter(BaseFactor):
    """使用ADX指标判断趋势强弱，当ADX低于阈值（如25）时视为无趋势震荡状态，易导致频繁止损和持仓超时。因子值在ADX低时接近-1，高时接近1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adx_filter",
            name="Trend Strength Filter",
            display_name="趋势强度过滤",
            description="使用ADX指标判断趋势强弱，当ADX低于阈值（如25）时视为无趋势震荡状态，易导致频繁止损和持仓超时。因子值在ADX低时接近-1，高时接近1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        # 计算+DM和-DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        # 平滑
        tr_smooth = tr.ewm(span=period, adjust=False).mean()
        pos_smooth = pd.Series(pos_dm).ewm(span=period, adjust=False).mean()
        neg_smooth = pd.Series(neg_dm).ewm(span=period, adjust=False).mean()
        pdi = 100 * pos_smooth / (tr_smooth + 1e-10)
        ndi = 100 * neg_smooth / (tr_smooth + 1e-10)
        adx = 100 * np.abs(pdi - ndi).ewm(span=period, adjust=False).mean() / (pdi + ndi + 1e-10)
        # 将ADX映射到[-1,1]，通常ADX范围0-100，阈值约25，以25为中心
        result = (adx - 25) / 25  # 当adx=0时-1, adx=50时1，超过50截断
        result = np.clip(result, -1, 1)
        return result
