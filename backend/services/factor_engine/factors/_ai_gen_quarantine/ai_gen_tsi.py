"""AI因子: 趋势强度指数 | 置信:70% | 基于价格动量的一致性来度量趋势强度。当趋势强度低时（类似低ADX环境），价格容易来回震荡，导致止损和超时亏损。使用方向性移动指数（DMI）思路简化，计算正向和负向方向性运动的差值绝对值，并平滑。值域[-1,1]：正值表示强上升趋势，负值表示强下降趋势，接近0表示震荡无趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Strength_Index(BaseFactor):
    """基于价格动量的一致性来度量趋势强度。当趋势强度低时（类似低ADX环境），价格容易来回震荡，导致止损和超时亏损。使用方向性移动指数（DMI）思路简化，计算正向和负向方向性运动的差值绝对值，并平滑。值域[-1,1]：正值表示强上升趋势，负值表示强下降趋势，接近0表示震荡无趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tsi",
            name="Trend Strength Index",
            display_name="趋势强度指数",
            description="基于价格动量的一致性来度量趋势强度。当趋势强度低时（类似低ADX环境），价格容易来回震荡，导致止损和超时亏损。使用方向性移动指数（DMI）思路简化，计算正向和负向方向性运动的差值绝对值，并平滑。值域[-1,1]：正值表示强上升趋势，负值表示强下降趋势，接近0表示震荡无趋势。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high, low, close = data['high'], data['low'], data['close']
        # 方向性运动
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 真实波幅
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        # 平滑
        period = 14
        smooth_plus = pd.Series(plus_dm).rolling(period).mean()
        smooth_minus = pd.Series(minus_dm).rolling(period).mean()
        smooth_tr = tr.rolling(period).mean()
        # 方向性指数
        di_plus = smooth_plus / (smooth_tr + 1e-10)
        di_minus = smooth_minus / (smooth_tr + 1e-10)
        # 趋势强度：方向性差值绝对值
        dx = np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10)
        adx = dx.rolling(period).mean()
        # 映射到[-1,1]，ADX高表示强趋势，但方向用DI差值决定
        direction = di_plus - di_minus
        result = pd.Series(np.tanh(direction * 2) * (1 - np.exp(-adx * 5)), index=data.index)
        return result
