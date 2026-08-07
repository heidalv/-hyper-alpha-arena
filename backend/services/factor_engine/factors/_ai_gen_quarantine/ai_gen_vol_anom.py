"""AI因子: 成交量异常因子 | 置信:60% | 检测成交量相对于过去一段时间均值的偏离程度，结合价格变动方向判断异常放量或缩量是否预示趋势反转。当成交量异常放大且价格方向相反时，输出-1（风险信号）；正常时输出0～+1（安全）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeAnomaly(BaseFactor):
    """检测成交量相对于过去一段时间均值的偏离程度，结合价格变动方向判断异常放量或缩量是否预示趋势反转。当成交量异常放大且价格方向相反时，输出-1（风险信号）；正常时输出0～+1（安全）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_anom",
            name="VolumeAnomaly",
            display_name="成交量异常因子",
            description="检测成交量相对于过去一段时间均值的偏离程度，结合价格变动方向判断异常放量或缩量是否预示趋势反转。当成交量异常放大且价格方向相反时，输出-1（风险信号）；正常时输出0～+1（安全）。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data: pd.DataFrame) -> pd.Series:
            import numpy as np
            import pandas as pd
            volume = data['volume']
            close = data['close']
            # 成交量均值和标准差
            vol_mean = volume.rolling(20).mean()
            vol_std = volume.rolling(20).std()
            # 成交量Z-score（防止除零）
            z = (volume - vol_mean) / (vol_std + 1e-10)
            # 归一化到[-1,1]，使用tanh
            z_norm = np.tanh(z * 0.5)
            # 价格变化方向
            price_change = close.pct_change()
            price_dir = np.sign(price_change)
            # 如果成交量异常大且价格方向与过去趋势相反，则风险高（-1）
            # 简化为：z>1.5且价格方向与过去20日趋势相反（用移动平均判断）
            trend = close - close.rolling(20).mean()
            trend_dir = np.sign(trend)
            # 风险信号：高成交量且与趋势相反
            anomaly_signal = (z_norm > 0.5) & (price_dir != trend_dir.shift(1))
            # 输出：异常时-1，否则用z_norm的正向部分
            result = np.where(anomaly_signal, -1.0, z_norm.clip(0, 1))
            return pd.Series(result, index=close.index).fillna(0)
