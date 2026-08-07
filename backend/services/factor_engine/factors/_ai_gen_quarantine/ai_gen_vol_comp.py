"""AI因子: 波动压缩因子 | 置信:60% | 通过布林带宽度相对于过去平均宽度衡量波动率压缩状态。当布林带宽度处于历史低位时，市场可能处于低波动震荡，此时趋势跟踪策略易触发持仓超时或止损。输出值[-1,1]，正值表示波动率扩张（适合趋势），负值表示压缩（需谨慎）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Squeeze_Indicator(BaseFactor):
    """通过布林带宽度相对于过去平均宽度衡量波动率压缩状态。当布林带宽度处于历史低位时，市场可能处于低波动震荡，此时趋势跟踪策略易触发持仓超时或止损。输出值[-1,1]，正值表示波动率扩张（适合趋势），负值表示压缩（需谨慎）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_comp",
            name="Volatility Squeeze Indicator",
            display_name="波动压缩因子",
            description="通过布林带宽度相对于过去平均宽度衡量波动率压缩状态。当布林带宽度处于历史低位时，市场可能处于低波动震荡，此时趋势跟踪策略易触发持仓超时或止损。输出值[-1,1]，正值表示波动率扩张（适合趋势），负值表示压缩（需谨慎）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        window = 20
        close = data['close']
        sma = close.rolling(window).mean()
        std = close.rolling(window).std()
        # 布林带宽度（两倍标准差除以均值归一化）
        bandwidth = (2 * std) / sma
        # 计算带宽的历史百分位（滚动窗口），以检测压缩
        hist_window = 60
        # 使用滚动分位数简化，或直接计算z-score
        bandwidth_mean = bandwidth.rolling(hist_window).mean()
        bandwidth_std = bandwidth.rolling(hist_window).std()
        # 标准化，取负值表示压缩（低带宽 -> 负值）
        z = (bandwidth - bandwidth_mean) / (bandwidth_std + 1e-8)
        # 映射到[-1,1]，使用tanh截断
        result = pd.Series(np.tanh(-z)).fillna(0)
        return result.clip(-1, 1)
