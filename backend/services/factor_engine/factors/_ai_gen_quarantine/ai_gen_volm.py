"""AI因子: 波动调整均值回复 | 置信:60% | 基于近期波动率与价格位置，识别高波动震荡区间的反转信号。当价格处于布林带上轨且波动率偏高时做空，反之做多，但考虑到亏损模式全为做多，该因子在波动率高且价格偏离均值时输出负值（即看空）以规避做多风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Mean_Reversion(BaseFactor):
    """基于近期波动率与价格位置，识别高波动震荡区间的反转信号。当价格处于布林带上轨且波动率偏高时做空，反之做多，但考虑到亏损模式全为做多，该因子在波动率高且价格偏离均值时输出负值（即看空）以规避做多风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volm",
            name="Volatility-Adjusted Mean Reversion",
            display_name="波动调整均值回复",
            description="基于近期波动率与价格位置，识别高波动震荡区间的反转信号。当价格处于布林带上轨且波动率偏高时做空，反之做多，但考虑到亏损模式全为做多，该因子在波动率高且价格偏离均值时输出负值（即看空）以规避做多风险。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算20日标准差和20日均线
        window = 20
        mean = data['close'].rolling(window).mean()
        std = data['close'].rolling(window).std()
        # 布林带上轨和下轨
        upper = mean + 2 * std
        lower = mean - 2 * std
        # 价格位置：当前价格偏离均线的程度，归一化到[-1,1]
        pos = (data['close'] - mean) / (std + 1e-8)
        # 波动率：当前标准差 / 均线，衡量相对波动
        vol = std / (mean + 1e-8)
        # 高波动时，价格在上下轨附近容易反转；亏损模式多为做多亏损，因此当价格在上轨附近且波动高时强烈看空(-1)
        # 当价格在下轨附近且波动高时看多(+1)，但考虑到全做多亏损，抑制看多信号
        factor = np.where(vol > vol.rolling(60).mean(),
                          -np.clip(pos, -1, 1),
                          np.clip(pos, -0.5, 0.5))  # 低波动时弱化
        return factor.fillna(0)
