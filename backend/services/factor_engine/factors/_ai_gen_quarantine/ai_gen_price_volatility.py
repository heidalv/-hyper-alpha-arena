"""AI因子: 价格波动率突变因子 | 置信:60% | 捕获短期波动率相对于长期波动率的突变，用于识别ai_reverse和master_running_close等反转/平仓模式。当短期波动率远高于长期（或远低于）时，发出负信号。计算方式：过去5周期真实波幅均值 / 过去30周期真实波幅均值，经tanh映射后取反。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceVolatilityRegimeChange(BaseFactor):
    """捕获短期波动率相对于长期波动率的突变，用于识别ai_reverse和master_running_close等反转/平仓模式。当短期波动率远高于长期（或远低于）时，发出负信号。计算方式：过去5周期真实波幅均值 / 过去30周期真实波幅均值，经tanh映射后取反。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_volatility",
            name="Price Volatility Regime Change",
            display_name="价格波动率突变因子",
            description="捕获短期波动率相对于长期波动率的突变，用于识别ai_reverse和master_running_close等反转/平仓模式。当短期波动率远高于长期（或远低于）时，发出负信号。计算方式：过去5周期真实波幅均值 / 过去30周期真实波幅均值，经tanh映射后取反。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        tr_short = tr.rolling(window=5, min_periods=3).mean()
        tr_long = tr.rolling(window=30, min_periods=15).mean()
        ratio = tr_short / (tr_long + 1e-10)
        # 如果ratio远大于1或远小于1都是异常，使用绝对值偏离1的程度
        dev = np.abs(ratio - 1) / 1  # 以1为基准
        # 用sigmoid-like映射到[-1,1]，但为了对称使用tanh，最大2倍偏差
        return -np.tanh(dev * 2)
