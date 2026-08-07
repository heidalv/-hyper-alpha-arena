"""AI因子: 趋势动量弱势 | 置信:60% | 通过比较短期均线斜率与长期均线斜率，结合ADX判断趋势强度。当短期斜率低于长期斜率且ADX低于阈值时，表示趋势弱且易反转，给出负信号。值域[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Momentum_Weakness(BaseFactor):
    """通过比较短期均线斜率与长期均线斜率，结合ADX判断趋势强度。当短期斜率低于长期斜率且ADX低于阈值时，表示趋势弱且易反转，给出负信号。值域[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendmom",
            name="Trend Momentum Weakness",
            display_name="趋势动量弱势",
            description="通过比较短期均线斜率与长期均线斜率，结合ADX判断趋势强度。当短期斜率低于长期斜率且ADX低于阈值时，表示趋势弱且易反转，给出负信号。值域[-1,1]。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 均线
        short_ma = close.rolling(10).mean()
        long_ma = close.rolling(30).mean()
        # 斜率（差分/均值归一化）
        short_slope = (short_ma - short_ma.shift(5)) / short_ma.shift(5)
        long_slope = (long_ma - long_ma.shift(10)) / long_ma.shift(10)
        # ADX 计算简化：使用TR和方向
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 简易ADX：用价格变化绝对值比ATR
        dm_plus = np.where(high > high.shift(1), high - high.shift(1), 0)
        dm_minus = np.where(low < low.shift(1), low.shift(1) - low, 0)
        di_plus = (dm_plus.rolling(14).sum() / (atr * 14 + 1e-10)) * 100
        di_minus = (dm_minus.rolling(14).sum() / (atr * 14 + 1e-10)) * 100
        dx = np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10) * 100
        adx = dx.rolling(14).mean()
        # 条件：短期斜率低于长期斜率（动量减弱）且ADX<25（趋势弱）
        weak_trend = (short_slope < long_slope) & (adx < 25)
        # 信号：弱趋势下给出负分（看空）
        signal = pd.Series(0.0, index=data.index)
        signal[weak_trend] = -1.0
        # 反向：如果短期斜率高于长期且ADX高则为正？但只返回负值符合亏损模式（多头亏损）
        # 平滑
        result = signal.rolling(5).mean().fillna(0.0)
        return result
