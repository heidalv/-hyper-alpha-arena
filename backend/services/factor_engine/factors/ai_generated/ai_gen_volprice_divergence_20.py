"""AI因子: 量价背离因子 | 置信:58% | 用20日价格动量与成交量动量的标准化差值衡量量价背离：价格上行但成交量萎缩（背离为负）常预示动能衰竭，价格下行但放量（背离为正）常预示承接。因子值越高代表量价配合越健康，未来上涨概率更高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence20(BaseFactor):
    """用20日价格动量与成交量动量的标准化差值衡量量价背离：价格上行但成交量萎缩（背离为负）常预示动能衰竭，价格下行但放量（背离为正）常预示承接。因子值越高代表量价配合越健康，未来上涨概率更高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprice_divergence_20",
            name="Volume Price Divergence 20",
            display_name="量价背离因子",
            description="用20日价格动量与成交量动量的标准化差值衡量量价背离：价格上行但成交量萎缩（背离为负）常预示动能衰竭，价格下行但放量（背离为正）常预示承接。因子值越高代表量价配合越健康，未来上涨概率更高。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        pr = data['close'].pct_change(20)
        vr = data['volume'].pct_change(20)
        pstd = data['close'].pct_change().rolling(20).std() + 1e-9
        vstd = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = ((pr / pstd) - (vr / vstd)).clip(-1, 1)
        return result
