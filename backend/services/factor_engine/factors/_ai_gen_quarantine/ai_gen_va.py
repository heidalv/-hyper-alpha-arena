"""AI因子: 成交量异常因子 | 置信:55% | 成交量异常放大或缩小可能预示假突破或流动性不足，容易导致开仓后快速反向波动。用当前成交量与过去20期均量的比值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Anomaly(BaseFactor):
    """成交量异常放大或缩小可能预示假突破或流动性不足，容易导致开仓后快速反向波动。用当前成交量与过去20期均量的比值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_va",
            name="Volume_Anomaly",
            display_name="成交量异常因子",
            description="成交量异常放大或缩小可能预示假突破或流动性不足，容易导致开仓后快速反向波动。用当前成交量与过去20期均量的比值。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 计算成交量与过去20期均量的比值
        vol_ma = data['volume'].rolling(20).mean()
        ratio = data['volume'] / vol_ma - 1.0
        # 压缩到[-1,1]，正向表示放量，负向表示缩量
        result = np.tanh(ratio * 2.0)
        # 处理前20期NaN
        return result.fillna(0.0)
