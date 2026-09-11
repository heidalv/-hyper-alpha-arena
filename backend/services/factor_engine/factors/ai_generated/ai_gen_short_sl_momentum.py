"""AI因子: 空头止损动量反转 | 置信:55% | 亏损中多次出现做空止损（sl），且集中在UNI、SOL、VIRTUAL等，说明在下跌趋势中盲目追空容易被反弹止损。此因子结合短期动量与长期趋势，当短期动量向上但长期趋势向下时，给出负值（避免做空或反向做多）；当短期动量向下且长期趋势也向下时，给出正值（顺势做空），过滤掉逆势追空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Shortstoplossmomentum(BaseFactor):
    """亏损中多次出现做空止损（sl），且集中在UNI、SOL、VIRTUAL等，说明在下跌趋势中盲目追空容易被反弹止损。此因子结合短期动量与长期趋势，当短期动量向上但长期趋势向下时，给出负值（避免做空或反向做多）；当短期动量向下且长期趋势也向下时，给出正值（顺势做空），过滤掉逆势追空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_sl_momentum",
            name="ShortStopLossMomentum",
            display_name="空头止损动量反转",
            description="亏损中多次出现做空止损（sl），且集中在UNI、SOL、VIRTUAL等，说明在下跌趋势中盲目追空容易被反弹止损。此因子结合短期动量与长期趋势，当短期动量向上但长期趋势向下时，给出负值（避免做空或反向做多）；当短期动量向下且长期趋势也向下时，给出正值（顺势做空），过滤掉逆势追空。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(3)
        long_ret = data['close'].pct_change(20)
        result = (short_ret - long_ret * 0.5).clip(-1, 1)
        return result
