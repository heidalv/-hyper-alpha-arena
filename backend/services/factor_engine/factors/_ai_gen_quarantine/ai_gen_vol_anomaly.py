"""AI因子: 成交量异常因子 | 置信:50% | 基于成交量的Z-score，当成交量出现异常放大或缩小超过2倍标准差时，可能预示着市场噪声或未知状态，因子给出负向信号；正常成交量时接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeAnomaly(BaseFactor):
    """基于成交量的Z-score，当成交量出现异常放大或缩小超过2倍标准差时，可能预示着市场噪声或未知状态，因子给出负向信号；正常成交量时接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_anomaly",
            name="Volume Anomaly",
            display_name="成交量异常因子",
            description="基于成交量的Z-score，当成交量出现异常放大或缩小超过2倍标准差时，可能预示着市场噪声或未知状态，因子给出负向信号；正常成交量时接近0。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算成交量对数变化率的Z-score
        vol_log = np.log(data['volume'] + 1)
        mean = vol_log.rolling(20).mean()
        std = vol_log.rolling(20).std()
        z = (vol_log - mean) / (std + 1e-10)
        # 对z进行clip，当|z|>2时给出负向信号，否则接近0
        result = -np.sign(z) * np.clip(np.abs(z) - 2, 0, 3) / 3
        return result.fillna(0).clip(-1, 1).astype(float)
