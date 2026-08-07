"""AI因子: 反转动量指数 | 置信:58% | 结合短期价格变化与均线偏离、K线形态（上影线/下影线）识别趋势衰竭点，针对错误模式中频繁出现的小幅亏损反转。计算收盘价相对于20日均线的百分比偏离，并用上下影线长度修正，输出-1（强烈看空反转）到+1（强烈看多反转）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalMomentumIndex(BaseFactor):
    """结合短期价格变化与均线偏离、K线形态（上影线/下影线）识别趋势衰竭点，针对错误模式中频繁出现的小幅亏损反转。计算收盘价相对于20日均线的百分比偏离，并用上下影线长度修正，输出-1（强烈看空反转）到+1（强烈看多反转）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rmi",
            name="Reversal Momentum Index",
            display_name="反转动量指数",
            description="结合短期价格变化与均线偏离、K线形态（上影线/下影线）识别趋势衰竭点，针对错误模式中频繁出现的小幅亏损反转。计算收盘价相对于20日均线的百分比偏离，并用上下影线长度修正，输出-1（强烈看空反转）到+1（强烈看多反转）。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
        open_ = data['open']
        # 均线偏离
        ma20 = close.rolling(20).mean()
        dev = (close - ma20) / ma20
        # 上下影线比例
        upper_shadow = high - np.maximum(close, open_)
        lower_shadow = np.minimum(close, open_) - low
        body = np.abs(close - open_)
        total = high - low + 1e-10
        upper_ratio = upper_shadow / total
        lower_ratio = lower_shadow / total
        # 反转信号：价格远离均线且出现长影线
        overbought = (dev > 0.03) & (upper_ratio > 0.6)
        oversold = (dev < -0.03) & (lower_ratio > 0.6)
        raw = np.where(oversold, 1, np.where(overbought, -1, 0))
        # 用偏离程度加权
        score = raw * (1 + np.abs(dev) * 5)
        result = pd.Series(score, index=close.index).fillna(0).clip(-1, 1)
        return result
