"""AI因子: 量价背离震荡因子 | 置信:65% | 当价格波动率偏低而成交量异常放大时，预示市场可能进入震荡或反转阶段，趋势策略容易止损。通过计算近期价格振幅与成交量分位数的背离程度，输出[-1,1]，正值表示量价背离严重（适合减仓或反向），负值表示正常趋势状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """当价格波动率偏低而成交量异常放大时，预示市场可能进入震荡或反转阶段，趋势策略容易止损。通过计算近期价格振幅与成交量分位数的背离程度，输出[-1,1]，正值表示量价背离严重（适合减仓或反向），负值表示正常趋势状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprice",
            name="Volume_Price_Divergence",
            display_name="量价背离震荡因子",
            description="当价格波动率偏低而成交量异常放大时，预示市场可能进入震荡或反转阶段，趋势策略容易止损。通过计算近期价格振幅与成交量分位数的背离程度，输出[-1,1]，正值表示量价背离严重（适合减仓或反向），负值表示正常趋势状态。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 输入data包含open,high,low,close,volume
        # 计算价格振幅：最高价与最低价之差除以收盘价
        amplitude = (data['high'] - data['low']) / data['close']
        # 计算成交量近N日分位数，N=20
        N = 20
        vol_rank = data['volume'].rolling(N, min_periods=N).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=False)
        # 振幅的滚动均值
        amp_ma = amplitude.rolling(N, min_periods=N).mean()
        # 偏离程度：低振幅 + 高成交量 => 背离
        divergence = (amp_ma - amplitude) * vol_rank  # 低振幅时(amp_ma - amplitude)正，高vol_rank正，相乘正
        # 标准化到[-1,1]
        result = (divergence - divergence.rolling(N, min_periods=N).mean()) / (divergence.rolling(N, min_periods=N).std() + 1e-10)
        result = result.clip(-3, 3) / 3
        return result.fillna(0)
