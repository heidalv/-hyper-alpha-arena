"""AI因子: 成交量异常因子 | 置信:60% | 检测成交量相对于20日均值的偏离程度。成交量显著偏离均值（过高或过低）可能预示市场状态转换，因子值趋向±1；正常成交量趋向0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Anomaly(BaseFactor):
    """检测成交量相对于20日均值的偏离程度。成交量显著偏离均值（过高或过低）可能预示市场状态转换，因子值趋向±1；正常成交量趋向0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_regime",
            name="Volume_Anomaly",
            display_name="成交量异常因子",
            description="检测成交量相对于20日均值的偏离程度。成交量显著偏离均值（过高或过低）可能预示市场状态转换，因子值趋向±1；正常成交量趋向0。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        volume = data['volume']
        vol_ma = volume.rolling(window=20).mean()
        ratio = volume / vol_ma
        # 取log，对称处理，然后用tanh映射到[-1,1]
        log_ratio = np.log(ratio + 1e-10)
        result = np.tanh(log_ratio * 2)  # 调节敏感度
        return result.fillna(0)
