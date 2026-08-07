"""AI因子: 趋势强度因子 | 置信:60% | 基于ADX原理的简化趋势强度指标，结合价格方向与波动率。当趋势强（ADX>25且方向一致）时值为正，否则为负。用于避免在横盘或弱趋势中持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthFactor(BaseFactor):
    """基于ADX原理的简化趋势强度指标，结合价格方向与波动率。当趋势强（ADX>25且方向一致）时值为正，否则为负。用于避免在横盘或弱趋势中持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tren",
            name="Trend Strength Factor",
            display_name="趋势强度因子",
            description="基于ADX原理的简化趋势强度指标，结合价格方向与波动率。当趋势强（ADX>25且方向一致）时值为正，否则为负。用于避免在横盘或弱趋势中持仓。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        period = 14
        # 计算+DM和-DM
        high = data['high']
        low = data['low']
        close = data['close']
        up_move = high.diff()
        down_move = low.diff() * -1
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=high.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=high.index)
        # 计算TR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        # 平滑
        atr = tr.rolling(period).mean()
        plus_di = 100 * plus_dm.rolling(period).sum() / atr
        minus_di = 100 * minus_dm.rolling(period).sum() / atr
        # DX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean()
        # 方向判断：+DI > -DI 为多头趋势，反之为空头
        trend_direction = (plus_di > minus_di).astype(int) * 2 - 1  # 1多头，-1空头
        # 强度映射：ADX>25时强度为正，否则为负
        strength = np.clip((adx - 20) / 10, -1, 1)  # 阈值20-30区间
        # 组合方向
        score = trend_direction * strength
        # 填充缺失
        result = score.fillna(0)
        return result
