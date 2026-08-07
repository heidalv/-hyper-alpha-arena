"""AI因子: 成交量异常因子 | 置信:60% | 计算当前成交量与过去N日平均成交量的偏离程度，结合价格方向。当成交量异常放大但价格未能有效突破时，预示反转风险。输出范围[-1,1]，负值表示成交量异常且价格无趋势，避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Anomaly(BaseFactor):
    """计算当前成交量与过去N日平均成交量的偏离程度，结合价格方向。当成交量异常放大但价格未能有效突破时，预示反转风险。输出范围[-1,1]，负值表示成交量异常且价格无趋势，避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volabnorm",
            name="Volume Anomaly",
            display_name="成交量异常因子",
            description="计算当前成交量与过去N日平均成交量的偏离程度，结合价格方向。当成交量异常放大但价格未能有效突破时，预示反转风险。输出范围[-1,1]，负值表示成交量异常且价格无趋势，避免做多。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol = data['volume']
        avg_vol = vol.rolling(20).mean()
        vol_ratio = vol / avg_vol
        price_change = data['close'].pct_change(5)
        raw = -np.sign(price_change) * (vol_ratio - 1)  # 负号：异常放量且价格不动->负信号
        norm = (raw - raw.rolling(50).mean()) / raw.rolling(50).std()
        result = norm.clip(-3, 3) / 3
        return result.fillna(0)
