"""AI因子: 趋势清晰度指数 | 置信:60% | 基于ADX指标评估趋势强弱，当ADX低于20时为无趋势状态，交易容易反复止损和超时。将ADX标准化到[-1,1]，负值表示趋势不清晰。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendclarityindex(BaseFactor):
    """基于ADX指标评估趋势强弱，当ADX低于20时为无趋势状态，交易容易反复止损和超时。将ADX标准化到[-1,1]，负值表示趋势不清晰。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_clarity",
            name="TrendClarityIndex",
            display_name="趋势清晰度指数",
            description="基于ADX指标评估趋势强弱，当ADX低于20时为无趋势状态，交易容易反复止损和超时。将ADX标准化到[-1,1]，负值表示趋势不清晰。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算+DI,-DI
        high = data['high']
        low = data['low']
        close = data['close']
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        # 真正波动+DM/-DM
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # ATR(14)
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(window=14, min_periods=14).mean()
        # 平滑处理
        plus_di = 100 * pd.Series(plus_dm).rolling(window=14).mean() / atr14
        minus_di = 100 * pd.Series(minus_dm).rolling(window=14).mean() / atr14
        # ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(window=14).mean()
        # 映射: 0-20 -> -1, 20-40 -> 线性, 40-100 -> 1
        def map_adx(x):
            if pd.isna(x):
                return 0
            if x < 20:
                return -1.0
            elif x > 40:
                return 1.0
            else:
                return (x - 30) / 10.0  # 在20-40之间线性从-1到1
        result = adx.apply(map_adx)
        return result
