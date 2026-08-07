"""AI因子: 量价背离 | 置信:65% | 当价格朝一个方向移动但成交量反向萎缩时，预示动能不足，容易导致持仓超时亏损。计算5日价格动量与成交量动量的背离程度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeDivergence(BaseFactor):
    """当价格朝一个方向移动但成交量反向萎缩时，预示动能不足，容易导致持仓超时亏损。计算5日价格动量与成交量动量的背离程度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vdiv",
            name="Volume Divergence",
            display_name="量价背离",
            description="当价格朝一个方向移动但成交量反向萎缩时，预示动能不足，容易导致持仓超时亏损。计算5日价格动量与成交量动量的背离程度。",
            category="volume",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        price_roc = close.pct_change(5)
        vol_roc = volume.pct_change(5)
        divergence = np.where((price_roc > 0) & (vol_roc < 0), -1, np.where((price_roc < 0) & (vol_roc < 0), 1, 0)).astype(float)
        result = pd.Series(divergence, index=data.index).fillna(0).clip(-1, 1)
        return result
