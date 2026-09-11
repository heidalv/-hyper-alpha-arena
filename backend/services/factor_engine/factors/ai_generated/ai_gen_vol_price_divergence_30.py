"""AI因子: 量价背离因子 | 置信:58% | 用近10日价格动量与成交量变化的标准化差值衡量量价背离：价格上涨但成交量萎缩（背离）预示动能衰竭，因子取负；价格下跌但放量（恐慌抛售）后易反弹，因子取正。通过滚动 z-score 标准化后截断，捕捉量价关系中的反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """用近10日价格动量与成交量变化的标准化差值衡量量价背离：价格上涨但成交量萎缩（背离）预示动能衰竭，因子取负；价格下跌但放量（恐慌抛售）后易反弹，因子取正。通过滚动 z-score 标准化后截断，捕捉量价关系中的反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence_30",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="用近10日价格动量与成交量变化的标准化差值衡量量价背离：价格上涨但成交量萎缩（背离）预示动能衰竭，因子取负；价格下跌但放量（恐慌抛售）后易反弹，因子取正。通过滚动 z-score 标准化后截断，捕捉量价关系中的反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        volchg = data['volume'].pct_change(10)
        retz = (ret - data['close'].pct_change(10).rolling(30).mean()) / (data['close'].pct_change(10).rolling(30).std() + 1e-9)
        volz = (volchg - data['volume'].pct_change(10).rolling(30).mean()) / (data['volume'].pct_change(10).rolling(30).std() + 1e-9)
        result = (retz - volz).rolling(5).mean().clip(-1, 1)
        return result
