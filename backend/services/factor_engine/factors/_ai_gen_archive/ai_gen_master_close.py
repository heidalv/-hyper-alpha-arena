"""AI因子: 主控平仓反向因子 | 置信:50% | 针对master_running_close亏损（通常为趋势反转），构造一个短期反转因子：当价格快速上涨后出现放量滞涨（量价背离），做空信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MasterCloseContrarian(BaseFactor):
    """针对master_running_close亏损（通常为趋势反转），构造一个短期反转因子：当价格快速上涨后出现放量滞涨（量价背离），做空信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_close",
            name="Master_Close_Contrarian",
            display_name="主控平仓反向因子",
            description="针对master_running_close亏损（通常为趋势反转），构造一个短期反转因子：当价格快速上涨后出现放量滞涨（量价背离），做空信号。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(3)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        price_change = data['close'].pct_change(1)
        result = ((ret > 0) & (vol_ratio > 1.5) & (price_change < 0)).astype(float) * -1
        result = result.fillna(0).rolling(5).mean().clip(-1, 1)
        return result
