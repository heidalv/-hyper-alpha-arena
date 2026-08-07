"""AI因子: 成交量衰竭背离因子 | 置信:60% | 利用价格与成交量的背离判断趋势衰竭。当价格上涨但成交量递减（量价背离），或价格下跌但成交量递增（恐慌性抛售），预示趋势可能反转。结合OBV（能量潮）与价格的关系生成信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeExhaustionDivergence(BaseFactor):
    """利用价格与成交量的背离判断趋势衰竭。当价格上涨但成交量递减（量价背离），或价格下跌但成交量递增（恐慌性抛售），预示趋势可能反转。结合OBV（能量潮）与价格的关系生成信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_exhaust",
            name="Volume Exhaustion Divergence",
            display_name="成交量衰竭背离因子",
            description="利用价格与成交量的背离判断趋势衰竭。当价格上涨但成交量递减（量价背离），或价格下跌但成交量递增（恐慌性抛售），预示趋势可能反转。结合OBV（能量潮）与价格的关系生成信号。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算OBV
        obv = (volume * (close.diff() > 0) - volume * (close.diff() < 0)).cumsum()
        # 价格和OBV的短期动量（5周期变化率）
        price_roc = close.pct_change(5)
        obv_roc = obv.pct_change(5)
        # 背离：价格涨但OBV跌（负背离，看跌），价格跌但OBV涨（正背离，看涨）
        bearish_div = (price_roc > 0.02) & (obv_roc < -0.02)
        bullish_div = (price_roc < -0.02) & (obv_roc > 0.02)
        factor = pd.Series(0.0, index=data.index)
        factor[bullish_div] = 1.0
        factor[bearish_div] = -1.0
        return factor
