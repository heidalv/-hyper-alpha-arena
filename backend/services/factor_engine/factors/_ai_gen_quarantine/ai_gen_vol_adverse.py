"""AI因子: 波动逆境动量 | 置信:60% | 当价格处于短期下跌趋势且波动率显著放大时，预示市场情绪恶化、止损触发概率增加，该因子输出负值以警示做多风险。计算：先求过去5日收益率（close/close.shift(5)-1）和过去10日平均真实波幅ATR(10)的Z-score，然后取两者乘积的负值，再经tanh映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adverse_Momentum(BaseFactor):
    """当价格处于短期下跌趋势且波动率显著放大时，预示市场情绪恶化、止损触发概率增加，该因子输出负值以警示做多风险。计算：先求过去5日收益率（close/close.shift(5)-1）和过去10日平均真实波幅ATR(10)的Z-score，然后取两者乘积的负值，再经tanh映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adverse",
            name="Volatility-Adverse Momentum",
            display_name="波动逆境动量",
            description="当价格处于短期下跌趋势且波动率显著放大时，预示市场情绪恶化、止损触发概率增加，该因子输出负值以警示做多风险。计算：先求过去5日收益率（close/close.shift(5)-1）和过去10日平均真实波幅ATR(10)的Z-score，然后取两者乘积的负值，再经tanh映射到[-1,1]。",
            category="composite",
            subcategory="volatility_momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        ret_period = 5
        atr_period = 10
        # 收益率
        ret = data['close'] / data['close'].shift(ret_period) - 1
        # 真实波幅
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(atr_period).mean()
        atr_z = (atr - atr.rolling(atr_period*2).mean()) / atr.rolling(atr_period*2).std()
        # 抑制极端值
        ret_clipped = ret.clip(-0.15, 0.15)
        # 构建因子：负的ret * atr_z，即下跌+高波动 -> 负值
        raw = -ret_clipped * atr_z
        # 归一化到[-1,1]
        result = np.tanh(raw)
        return result.fillna(0)
