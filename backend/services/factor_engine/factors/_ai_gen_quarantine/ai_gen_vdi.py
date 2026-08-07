"""AI因子: 量价背离 | 置信:65% | 当价格上涨但成交量持续萎缩，或价格下跌但成交量放大，预示趋势不可持续。因子计算价格方向与成交量方向的背离程度，返回-1到1，负值表示上涨无量（多头风险大）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence(BaseFactor):
    """当价格上涨但成交量持续萎缩，或价格下跌但成交量放大，预示趋势不可持续。因子计算价格方向与成交量方向的背离程度，返回-1到1，负值表示上涨无量（多头风险大）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vdi",
            name="Volume_Price_Divergence",
            display_name="量价背离",
            description="当价格上涨但成交量持续萎缩，或价格下跌但成交量放大，预示趋势不可持续。因子计算价格方向与成交量方向的背离程度，返回-1到1，负值表示上涨无量（多头风险大）。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格短期变化率
        ret = close.pct_change(5)
        # 成交量短期变化率
        vol_ret = volume.pct_change(5)
        # 背离：价格涨但成交量跌，或价格跌但成交量涨
        divergence = -ret.sign() * vol_ret.sign() * (abs(ret) + abs(vol_ret)) / 2
        factor = divergence.clip(-1, 1)
        return factor
