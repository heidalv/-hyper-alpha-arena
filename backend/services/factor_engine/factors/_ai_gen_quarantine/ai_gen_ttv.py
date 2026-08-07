"""AI因子: 时间波动失配 | 置信:50% | 衡量当前市场波动率与历史平均波动率的偏离程度，结合持仓时间因素（隐含通过滚动窗口）。当波动率快速下降而持仓周期未适应时，容易产生超时亏损。因子值负向表示波动率过低（可能引发持仓超时），正向表示波动率适中。利用近期ATR与长期ATR比值，取负对数后映射。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Time_Volatility_Mismatch(BaseFactor):
    """衡量当前市场波动率与历史平均波动率的偏离程度，结合持仓时间因素（隐含通过滚动窗口）。当波动率快速下降而持仓周期未适应时，容易产生超时亏损。因子值负向表示波动率过低（可能引发持仓超时），正向表示波动率适中。利用近期ATR与长期ATR比值，取负对数后映射。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ttv",
            name="Time-Volatility Mismatch",
            display_name="时间波动失配",
            description="衡量当前市场波动率与历史平均波动率的偏离程度，结合持仓时间因素（隐含通过滚动窗口）。当波动率快速下降而持仓周期未适应时，容易产生超时亏损。因子值负向表示波动率过低（可能引发持仓超时），正向表示波动率适中。利用近期ATR与长期ATR比值，取负对数后映射。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 短期ATR（5周期）和长期ATR（60周期）
        tr = pd.concat([data['high'] - data['low'], (data['high'] - data['close'].shift(1)).abs(), (data['low'] - data['close'].shift(1)).abs()], axis=1).max(axis=1)
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(60).mean()
        # 比值 r = short / long, 稳定时 r≈1
        ratio = atr_short / (atr_long + 1e-10)
        # 取对数偏离，再映射到[-1,1]: log(ratio) 范围 -inf~inf, 用tanh缩放到(-1,1)
        log_ratio = np.log(ratio + 1e-10)
        result = np.tanh(log_ratio * 2)
        # 当ratio远小于1时，log_ratio为负，输出负值提示波动偏低
        return result
