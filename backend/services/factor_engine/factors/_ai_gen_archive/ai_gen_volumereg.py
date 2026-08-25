"""AI因子: 成交量异常检测 | 置信:55% | 识别成交量相对历史均值的异常程度：极低成交量（dust_cleanup典型特征）或成交量突然萎缩，容易导致滑点和流动性问题，返回负值；成交量正常且稳定时接近0或正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeRegimeAnomaly(BaseFactor):
    """识别成交量相对历史均值的异常程度：极低成交量（dust_cleanup典型特征）或成交量突然萎缩，容易导致滑点和流动性问题，返回负值；成交量正常且稳定时接近0或正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumereg",
            name="Volume_Regime_Anomaly",
            display_name="成交量异常检测",
            description="识别成交量相对历史均值的异常程度：极低成交量（dust_cleanup典型特征）或成交量突然萎缩，容易导致滑点和流动性问题，返回负值；成交量正常且稳定时接近0或正值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        volume = data['volume']
        close = data['close']
        # 计算成交量滚动中位数和标准差（50日窗口）
        med = volume.rolling(50, min_periods=10).median().fillna(method='bfill').fillna(volume.mean())
        std = volume.rolling(50, min_periods=10).std().fillna(method='bfill').fillna(volume.std())
        # 标准化Z-score
        z = (volume - med) / (std + 1e-8)
        # 对极低成交量（z < -1.5）惩罚，对极高成交量（可能异常）也适当惩罚，但重点在低量
        # 用sigmoid映射到[-1,1]：低量负值，正常0附近，高量正但不过度
        raw = np.where(z < -1.0, -1.0, np.where(z < 0, z * 0.5, np.tanh(z * 0.2)))
        result = np.clip(raw, -1, 1)
        return pd.Series(result, index=data.index)
