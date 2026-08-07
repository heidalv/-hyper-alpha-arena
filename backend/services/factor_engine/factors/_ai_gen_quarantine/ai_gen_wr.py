"""AI因子: 弱势反弹 | 置信:60% | 识别价格从下跌后出现的弱势反弹，此类反弹常缺乏成交量支持，容易转为继续下跌，导致做多止损。因子计算近N日价格反弹幅度与前期跌幅的比值，同时考虑成交量萎缩程度。若反弹力度弱且量能萎缩，则输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Weak_Retracement(BaseFactor):
    """识别价格从下跌后出现的弱势反弹，此类反弹常缺乏成交量支持，容易转为继续下跌，导致做多止损。因子计算近N日价格反弹幅度与前期跌幅的比值，同时考虑成交量萎缩程度。若反弹力度弱且量能萎缩，则输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wr",
            name="Weak Retracement",
            display_name="弱势反弹",
            description="识别价格从下跌后出现的弱势反弹，此类反弹常缺乏成交量支持，容易转为继续下跌，导致做多止损。因子计算近N日价格反弹幅度与前期跌幅的比值，同时考虑成交量萎缩程度。若反弹力度弱且量能萎缩，则输出负值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        n = 14
        # 计算近期最低点和最高点
        low_n = df['low'].rolling(n).min()
        high_n = df['high'].rolling(n).max()
        # 价格从低点反弹的百分比
        bounce = (df['close'] - low_n) / (low_n + 1e-10)
        # 前期跌幅（最高点到最低点的跌幅）
        drawdown = (high_n - low_n) / (high_n + 1e-10)
        # 反弹强度 = 反弹幅度 / 前期跌幅
        bounce_strength = bounce / (drawdown + 1e-10)
        # 成交量变化：当前成交量与过去N日平均成交量对比
        vol_avg = df['volume'].rolling(n).mean()
        vol_ratio = df['volume'] / (vol_avg + 1e-10)
        # 弱势反弹条件：反弹幅度小于阈值（比如0.3倍前期跌幅）且成交量萎缩
        weak_bounce = (bounce_strength < 0.3) & (vol_ratio < 0.8)
        raw = -weak_bounce.astype(float)
        result = pd.Series(np.tanh(raw * 5), index=df.index).fillna(0)
        return result
