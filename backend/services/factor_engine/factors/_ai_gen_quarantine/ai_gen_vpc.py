"""AI因子: 量价协同因子 | 置信:60% | 结合价格变化和成交量变化的方向一致性。当价格上涨且成交量放大，或价格下跌且成交量放大时因子为正(趋势确认); 当价格与成交量背离时(如价涨量缩)因子为负(趋势不可持续)，用于识别regime=unknown环境下的虚假信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Confluence(BaseFactor):
    """结合价格变化和成交量变化的方向一致性。当价格上涨且成交量放大，或价格下跌且成交量放大时因子为正(趋势确认); 当价格与成交量背离时(如价涨量缩)因子为负(趋势不可持续)，用于识别regime=unknown环境下的虚假信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vpc",
            name="Volume_Price_Confluence",
            display_name="量价协同因子",
            description="结合价格变化和成交量变化的方向一致性。当价格上涨且成交量放大，或价格下跌且成交量放大时因子为正(趋势确认); 当价格与成交量背离时(如价涨量缩)因子为负(趋势不可持续)，用于识别regime=unknown环境下的虚假信号。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 价格变化方向 (1:上涨, -1:下跌, 0:平)
        price_change = np.sign(data['close'].diff()).fillna(0)
        # 成交量变化方向 (成交量相对20日均量的变化)
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma20
        # 成交量相对于均值: >1为放量，<1为缩量
        vol_signal = np.where(vol_ratio > 1.05, 1, np.where(vol_ratio < 0.95, -1, 0))
        # 协同: 价格和成交量方向相同则正，相反则负，无方向则0
        factor = price_change * vol_signal
        # 压缩到[-1,1]
        factor = np.clip(factor, -1, 1)
        return factor.fillna(0)
