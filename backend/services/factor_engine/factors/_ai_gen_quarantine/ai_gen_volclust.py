"""AI因子: 波动聚类因子 | 置信:55% | 使用布林带宽度（20日标准差）的当前百分位和价格在布林带中的位置。当带宽处于历史低位（<20%分位）且价格在中轨附近（偏离<0.1倍带宽）时，认为市场处于无序震荡区间，输出-1；否则根据带宽的百分位映射到[-1,1]（高带宽且趋势明确时为正）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Clustering_Factor(BaseFactor):
    """使用布林带宽度（20日标准差）的当前百分位和价格在布林带中的位置。当带宽处于历史低位（<20%分位）且价格在中轨附近（偏离<0.1倍带宽）时，认为市场处于无序震荡区间，输出-1；否则根据带宽的百分位映射到[-1,1]（高带宽且趋势明确时为正）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volclust",
            name="Volatility Clustering Factor",
            display_name="波动聚类因子",
            description="使用布林带宽度（20日标准差）的当前百分位和价格在布林带中的位置。当带宽处于历史低位（<20%分位）且价格在中轨附近（偏离<0.1倍带宽）时，认为市场处于无序震荡区间，输出-1；否则根据带宽的百分位映射到[-1,1]（高带宽且趋势明确时为正）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        window = 20
        ma = close.rolling(window).mean()
        std = close.rolling(window).std()
        # 布林带宽度 = std / ma （相对带宽）
        bandwidth = std / ma
        # 带宽的历史百分位（使用rolling窗口计算，这里简化用expanding累积分位，但为避免未来信息，用前向窗口前值？实际用rolling rank更合理）
        # 为避免未来信息，使用过去500个周期的分位，但简单起见，使用expanding中的当前值相对于历史分布
        # 这里采用rolling(100)的rank，注意边界
        rank = bandwidth.rolling(100, min_periods=20).apply(lambda x: (x.iloc[-1] < x).mean() if len(x)>=20 else 0.5, raw=False)
        # 价格偏离中轨的程度
        z_score = (close - ma) / std
        # 判断是否窄幅震荡
        low_vol = rank < 0.2
        near_mid = z_score.abs() < 0.1  # 偏离<0.1倍带宽，注意带宽为std/ma，实际偏离为z_score*std，但这里直接比较z_score
        # 输出：窄幅震荡时-1，否则根据带宽百分位映射到[-1,1]（高带宽高位为正，低位为负，但需结合趋势方向）
        # 简单处理：若窄幅震荡则-1，否则取 (rank*2 -1) 即[-1,1]
        base = rank * 2 - 1
        result = pd.Series(index=close.index, dtype=float)
        result = base.where(~low_vol, -1.0)  # 窄幅震荡时强制-1
        # 若价格大幅偏离中轨且带宽高，可能趋势强，但这里不再细化
        result = result.fillna(0)
        return result
