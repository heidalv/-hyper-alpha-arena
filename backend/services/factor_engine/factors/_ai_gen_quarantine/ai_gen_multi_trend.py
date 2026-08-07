"""AI因子: 多周期趋势一致性因子 | 置信:65% | 计算短期(5日)、中期(20日)、长期(60日)简单移动平均线的方向。当三者方向一致（同涨或同跌）时视为趋势明确，否则视为regime unknown，信号为负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Consistency(BaseFactor):
    """计算短期(5日)、中期(20日)、长期(60日)简单移动平均线的方向。当三者方向一致（同涨或同跌）时视为趋势明确，否则视为regime unknown，信号为负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_multi_trend",
            name="Multi_Timeframe_Consistency",
            display_name="多周期趋势一致性因子",
            description="计算短期(5日)、中期(20日)、长期(60日)简单移动平均线的方向。当三者方向一致（同涨或同跌）时视为趋势明确，否则视为regime unknown，信号为负。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        close = df['close']
        ma_short = close.rolling(5).mean()
        ma_medium = close.rolling(20).mean()
        ma_long = close.rolling(60).mean()
        # 方向：1为上涨，0为下跌，用差分符号
        dir_short = np.sign(ma_short.diff())
        dir_medium = np.sign(ma_medium.diff())
        dir_long = np.sign(ma_long.diff())
        # 一致性：三者同号则为1，否则为-1
        consistency = np.where((dir_short == dir_medium) & (dir_medium == dir_long), 1, -1)
        # 处理NaN
        result = pd.Series(consistency, index=df.index).fillna(0)
        return result
