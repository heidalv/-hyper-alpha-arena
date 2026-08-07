"""AI因子: 假突破因子 | 置信:60% | 捕捉价格突破近期高低点但缺乏后续动量的模式。计算当前价格与过去N周期高/低点的距离，结合成交量是否萎缩。当突破但成交量下降时，标记为假突破，给出反向信号。适合做空假突破或做多假跌破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PseudoBreakoutFactor(BaseFactor):
    """捕捉价格突破近期高低点但缺乏后续动量的模式。计算当前价格与过去N周期高/低点的距离，结合成交量是否萎缩。当突破但成交量下降时，标记为假突破，给出反向信号。适合做空假突破或做多假跌破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pseudo_break",
            name="Pseudo-Breakout Factor",
            display_name="假突破因子",
            description="捕捉价格突破近期高低点但缺乏后续动量的模式。计算当前价格与过去N周期高/低点的距离，结合成交量是否萎缩。当突破但成交量下降时，标记为假突破，给出反向信号。适合做空假突破或做多假跌破。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 参数
        period = 20
        # 计算过去period的最高价与最低价
        high = data['high'].rolling(period).max()
        low = data['low'].rolling(period).min()
        # 判断当前收盘是否突破
        above_high = data['close'] > high.shift(1)
        below_low = data['close'] < low.shift(1)
        # 计算成交量变化（与过去平均相比）
        avg_vol = data['volume'].rolling(period).mean()
        vol_ratio = data['volume'] / avg_vol
        # 假突破信号：突破但成交量低于平均
        fake_bull = above_high & (vol_ratio < 0.8)
        fake_bear = below_low & (vol_ratio < 0.8)
        # 生成因子值：假多头突破给负值（做空），假空头突破给正值（做多）
        result = pd.Series(0.0, index=data.index)
        result[fake_bull] = -0.5
        result[fake_bear] = 0.5
        # 使用动量微调强度
        # 返回连续值
        return result
