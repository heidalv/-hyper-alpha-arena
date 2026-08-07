"""AI因子: 成交量秩序紊乱 | 置信:65% | 识别成交量与价格波动不匹配的异常状态。计算最近N根K线的成交量百分位与ATR百分位的差值，当成交量异常放大而波动率极低时，表明市场处于无序状态（regime=unknown）。输出经过tanh归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Order_Disorder(BaseFactor):
    """识别成交量与价格波动不匹配的异常状态。计算最近N根K线的成交量百分位与ATR百分位的差值，当成交量异常放大而波动率极低时，表明市场处于无序状态（regime=unknown）。输出经过tanh归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_ord",
            name="Volume_Order_Disorder",
            display_name="成交量秩序紊乱",
            description="识别成交量与价格波动不匹配的异常状态。计算最近N根K线的成交量百分位与ATR百分位的差值，当成交量异常放大而波动率极低时，表明市场处于无序状态（regime=unknown）。输出经过tanh归一化到[-1,1]。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 参数
        n = 30
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(n).mean()
        # 计算成交量百分位
        vol_rank = data['volume'].rolling(n).rank(pct=True)
        # 计算ATR百分位
        atr_rank = atr.rolling(n).rank(pct=True)
        # 差值：成交量异常高但ATR异常低 => 正值
        diff = vol_rank - atr_rank
        # 标准化到[-1,1] 使用tanh
        result = np.tanh(diff * 3)
        return result.fillna(0.0).clip(-1.0, 1.0)
