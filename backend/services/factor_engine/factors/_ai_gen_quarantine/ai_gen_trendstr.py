"""AI因子: 简化ADX趋势强度 | 置信:60% | 基于方向性运动指标（+DI和-DI）的差值绝对值计算趋势强度，避免在无趋势市场中追涨杀跌。输出正值为上升趋势强，负值为下降趋势强，接近0为震荡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendstrengthadx(BaseFactor):
    """基于方向性运动指标（+DI和-DI）的差值绝对值计算趋势强度，避免在无趋势市场中追涨杀跌。输出正值为上升趋势强，负值为下降趋势强，接近0为震荡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendstr",
            name="TrendStrengthADX",
            display_name="简化ADX趋势强度",
            description="基于方向性运动指标（+DI和-DI）的差值绝对值计算趋势强度，避免在无趋势市场中追涨杀跌。输出正值为上升趋势强，负值为下降趋势强，接近0为震荡。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算真实波幅TR
        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        # 方向性运动
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 平滑14周期
        period = 14
        tr_smooth = tr.rolling(period, min_periods=period).mean()
        plus_smooth = pd.Series(plus_dm).rolling(period, min_periods=period).mean()
        minus_smooth = pd.Series(minus_dm).rolling(period, min_periods=period).mean()
        # +DI和-DI
        plus_di = 100 * plus_smooth / (tr_smooth + 1e-10)
        minus_di = 100 * minus_smooth / (tr_smooth + 1e-10)
        # 趋势强度：差值归一化到[-1,1]
        diff = plus_di - minus_di
        strength = diff / 100.0  # 理论范围[-100,100]除以100
        result = strength.clip(-1, 1).fillna(0).values
        return result
