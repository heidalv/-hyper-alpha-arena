"""AI因子: 趋势衰竭指标 | 置信:50% | 检测连续同向走势后的反转预期。计算连续上涨/下跌天数，结合价格波动率衰减，当趋势持续很久但涨幅/跌幅逐渐缩小时，预示反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustionIndicator(BaseFactor):
    """检测连续同向走势后的反转预期。计算连续上涨/下跌天数，结合价格波动率衰减，当趋势持续很久但涨幅/跌幅逐渐缩小时，预示反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_exhaust",
            name="Trend Exhaustion Indicator",
            display_name="趋势衰竭指标",
            description="检测连续同向走势后的反转预期。计算连续上涨/下跌天数，结合价格波动率衰减，当趋势持续很久但涨幅/跌幅逐渐缩小时，预示反转。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 连续上涨/下跌计数
        up = (close.diff() > 0).astype(int)
        down = (close.diff() < 0).astype(int)
        # 利用滚动求和标记连续同向天数
        streak_up = up * (up.groupby((up != up.shift()).cumsum()).cumcount() + 1)
        streak_down = down * (down.groupby((down != down.shift()).cumsum()).cumcount() + 1)
        # 取绝对值大的方向
        streak = np.where(streak_up > streak_down, streak_up, -streak_down)
        # 计算最近几日的价格变化幅度（标准化）
        ret = close.pct_change()
        vol_ma = ret.abs().rolling(10).mean().replace(0, np.nan)
        recent_ret = (close - close.shift(5)) / close.shift(5)
        # 衰减信号：连续同向但近期涨幅变小
        # 若streak正向>3且最近5日涨幅<过去10日平均涨幅的一半 -> 衰竭
        # 简化：使用streak的绝对值乘以一个方向相反的因子
        exhaustion = np.sign(streak) * np.clip(np.abs(streak) / 10.0, 0, 1)  # 趋势强度因子
        # 反转信号：趋势强但近期动量减弱
        momentum_slowing = (ret.rolling(3).mean() - ret.rolling(10).mean()).fillna(0)
        # 组合：趋势强度大且动量减缓时，取反方向
        signal = -exhaustion * np.tanh(momentum_slowing * 5)
        result = pd.Series(signal, index=data.index).fillna(0)
        return result.clip(-1, 1)
