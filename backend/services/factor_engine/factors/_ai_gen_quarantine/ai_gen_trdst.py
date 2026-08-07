"""AI因子: 趋势强度 | 置信:60% | 基于ADX思想简化：计算过去N周期内的方向性运动（+DM/-DM）的绝对值之和与真实波幅的比值，衡量趋势强度。值越接近+1表示强上升趋势，越接近-1表示强下降趋势，接近0表示震荡。用于过滤震荡市中的假信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendstrength(BaseFactor):
    """基于ADX思想简化：计算过去N周期内的方向性运动（+DM/-DM）的绝对值之和与真实波幅的比值，衡量趋势强度。值越接近+1表示强上升趋势，越接近-1表示强下降趋势，接近0表示震荡。用于过滤震荡市中的假信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trdst",
            name="TrendStrength",
            display_name="趋势强度",
            description="基于ADX思想简化：计算过去N周期内的方向性运动（+DM/-DM）的绝对值之和与真实波幅的比值，衡量趋势强度。值越接近+1表示强上升趋势，越接近-1表示强下降趋势，接近0表示震荡。用于过滤震荡市中的假信号。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 14
        high = data['high'].values
        low = data['low'].values
        close = data['close'].values
        # 计算True Range
        tr = np.maximum(high[1:] - low[1:], np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1]))
        # 计算+DM和-DM
        up_move = high[1:] - high[:-1]
        down_move = low[:-1] - low[1:]
        pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 平滑（简单移动平均）
        def sma(x, n):
            cumsum = np.cumsum(np.insert(x, 0, 0))
            return (cumsum[n:] - cumsum[:-n]) / n
        tr_smooth = sma(tr, n)
        pos_dm_smooth = sma(pos_dm, n)
        neg_dm_smooth = sma(neg_dm, n)
        # 计算方向指标
        pdi = 100 * pos_dm_smooth / tr_smooth
        ndi = 100 * neg_dm_smooth / tr_smooth
        dx = 100 * np.abs(pdi - ndi) / (pdi + ndi + 1e-10)
        adx = sma(dx, n)
        # 填充前导NaN
        result = np.full(len(close), np.nan)
        result[-len(adx):] = adx
        # 映射到[-1,1]：趋势强度用ADX归一化到0-100，再映射到0-1，然后乘以方向
        direction = np.sign(pdi - ndi)
        direction = np.append(np.full(n*2-1, np.nan), direction)  # 对齐长度
        adx_scaled = np.nan_to_num(result / 100.0) * 2 - 1  # 0->-1, 100->1
        # 最终用方向修饰
        final = adx_scaled * np.nan_to_num(direction, nan=0)
        return pd.Series(final, index=data.index)
