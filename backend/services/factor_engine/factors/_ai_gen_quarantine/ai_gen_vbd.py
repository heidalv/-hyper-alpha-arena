"""AI因子: 量价背离 | 置信:65% | 检测价格创新高但成交量未能同步放大的背离现象。在上升趋势中，如果成交量萎缩，可能预示动能衰竭，做多风险增加。该因子计算过去N日内价格高点对应的成交量相对均值的偏离程度，并转为负向信号。结果[-1,1]：负值越大表示背离越严重。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence(BaseFactor):
    """检测价格创新高但成交量未能同步放大的背离现象。在上升趋势中，如果成交量萎缩，可能预示动能衰竭，做多风险增加。该因子计算过去N日内价格高点对应的成交量相对均值的偏离程度，并转为负向信号。结果[-1,1]：负值越大表示背离越严重。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vbd",
            name="Volume-Price Divergence",
            display_name="量价背离",
            description="检测价格创新高但成交量未能同步放大的背离现象。在上升趋势中，如果成交量萎缩，可能预示动能衰竭，做多风险增加。该因子计算过去N日内价格高点对应的成交量相对均值的偏离程度，并转为负向信号。结果[-1,1]：负值越大表示背离越严重。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        window = 14
        high = data['high']
        volume = data['volume']
        # 找到最近window日内的最高价位置
        rolling_max = high.rolling(window).max()
        is_new_high = (high == rolling_max) & (high.shift(1) < rolling_max)  # 当天创window内新高
        # 成交量相对过去window均值的比值
        vol_ma = volume.rolling(window).mean().shift(1)  # 前一天均值
        vol_ratio = volume / (vol_ma + 1e-10)
        # 对于新高日，如果成交量比值小于1，则视为背离，值越小背离越严重
        divergence = pd.Series(0, index=data.index)
        mask = is_new_high & (vol_ratio < 0.8)  # 成交量比均值低20%以上
        divergence[mask] = (vol_ratio[mask] - 0.8) / 0.8  # 范围-1到0
        # 平滑处理
        divergence = divergence.rolling(3).mean().fillna(0)
        return divergence.clip(-1, 0)
