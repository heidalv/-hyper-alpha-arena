"""AI因子: 趋势一致性因子 | 置信:60% | 基于短期(5日)、中期(10日)、长期(20日)简单移动平均的方向一致性。如果三者方向一致向上则接近+1，一致向下则接近-1，混乱则接近0。用于识别市场是否处于明确趋势状态，避免在regime unknown时交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Consistency_Factor(BaseFactor):
    """基于短期(5日)、中期(10日)、长期(20日)简单移动平均的方向一致性。如果三者方向一致向上则接近+1，一致向下则接近-1，混乱则接近0。用于识别市场是否处于明确趋势状态，避免在regime unknown时交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trdcon",
            name="Trend Consistency Factor",
            display_name="趋势一致性因子",
            description="基于短期(5日)、中期(10日)、长期(20日)简单移动平均的方向一致性。如果三者方向一致向上则接近+1，一致向下则接近-1，混乱则接近0。用于识别市场是否处于明确趋势状态，避免在regime unknown时交易。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        close = data['close']
        # 计算均线
        ma5 = close.rolling(5).mean()
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()
        # 计算斜率（方向）：当前值相对于前一根的值，1表示上升，-1下降，0持平
        def direction(series):
            diff = series.diff()
            dir_ = diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
            return dir_
        dir5 = direction(ma5)
        dir10 = direction(ma10)
        dir20 = direction(ma20)
        # 方向总和，范围[-3,3]
        sum_dir = dir5 + dir10 + dir20
        # 映射到[-1,1]：除以3
        result = sum_dir / 3.0
        # 前20个周期无数据填充0
        result = result.fillna(0)
        return result
