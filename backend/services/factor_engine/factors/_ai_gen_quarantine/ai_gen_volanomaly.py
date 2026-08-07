"""AI因子: 成交量异常检测 | 置信:60% | 检测当前成交量相对于近期均值与标准差的偏离程度。当成交量异常放大时，可能预示市场状态突变；异常缩量则可能流动性枯竭。因子值为正表示放量，负表示缩量，绝对值越大异常程度越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeAnomalyDetector(BaseFactor):
    """检测当前成交量相对于近期均值与标准差的偏离程度。当成交量异常放大时，可能预示市场状态突变；异常缩量则可能流动性枯竭。因子值为正表示放量，负表示缩量，绝对值越大异常程度越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volanomaly",
            name="Volume Anomaly Detector",
            display_name="成交量异常检测",
            description="检测当前成交量相对于近期均值与标准差的偏离程度。当成交量异常放大时，可能预示市场状态突变；异常缩量则可能流动性枯竭。因子值为正表示放量，负表示缩量，绝对值越大异常程度越高。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        volume = data['volume']
        window = 20
        vol_mean = volume.rolling(window).mean()
        vol_std = volume.rolling(window).std()
        z_score = (volume - vol_mean) / (vol_std + 1e-10)
        # 截断并映射到[-1,1]
        result = np.clip(z_score / 3.0, -1, 1)  # 3倍标准差内线性映射
        return result
