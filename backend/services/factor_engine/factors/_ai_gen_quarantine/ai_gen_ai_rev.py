"""AI因子: AI反转背离因子 | 置信:60% | 模拟ai_reverse亏损模式，识别价格与成交量的背离现象。当价格短期动量向下但成交量萎缩时，预示下跌动能减弱，可能反转。计算6日价格动量（close - close.shift(6)）与6日成交量变化率之间的滚动相关系数，取其负值作为信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AIReverseDivergenceFactor(BaseFactor):
    """模拟ai_reverse亏损模式，识别价格与成交量的背离现象。当价格短期动量向下但成交量萎缩时，预示下跌动能减弱，可能反转。计算6日价格动量（close - close.shift(6)）与6日成交量变化率之间的滚动相关系数，取其负值作为信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ai_rev",
            name="AI Reverse Divergence Factor",
            display_name="AI反转背离因子",
            description="模拟ai_reverse亏损模式，识别价格与成交量的背离现象。当价格短期动量向下但成交量萎缩时，预示下跌动能减弱，可能反转。计算6日价格动量（close - close.shift(6)）与6日成交量变化率之间的滚动相关系数，取其负值作为信号。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 价格动量
        price_mom = df['close'] - df['close'].shift(6)
        # 成交量变化率
        vol_chg = df['volume'].pct_change(6)
        # 滚动相关系数（窗口12）
        corr = price_mom.rolling(12).corr(vol_chg)
        # 负相关表示背离：价格下跌时成交量下降，预期反转向上
        signal = -corr
        # 处理NaN并标准化到[-1,1]
        result = signal.fillna(0).clip(-1, 1)
        return result
