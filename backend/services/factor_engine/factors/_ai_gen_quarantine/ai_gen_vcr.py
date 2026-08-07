"""AI因子: 成交量确认因子 | 置信:60% | 检测价格突破时是否有成交量配合。当价格突破近期高点或低点但成交量未显著放大时，可能出现假突破导致止损，因子值为负；当突破伴随放量时，因子值为正。因子计算基于收盘价相对于前期高/低点的位置与成交量变化率的比值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmationRatio(BaseFactor):
    """检测价格突破时是否有成交量配合。当价格突破近期高点或低点但成交量未显著放大时，可能出现假突破导致止损，因子值为负；当突破伴随放量时，因子值为正。因子计算基于收盘价相对于前期高/低点的位置与成交量变化率的比值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcr",
            name="Volume Confirmation Ratio",
            display_name="成交量确认因子",
            description="检测价格突破时是否有成交量配合。当价格突破近期高点或低点但成交量未显著放大时，可能出现假突破导致止损，因子值为负；当突破伴随放量时，因子值为正。因子计算基于收盘价相对于前期高/低点的位置与成交量变化率的比值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np

        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']

        # 前20日最高价和最低价
        recent_high = high.rolling(20).max()
        recent_low = low.rolling(20).min()

        # 当前价格相对位置（0到1）
        price_pos = (close - recent_low) / (recent_high - recent_low + 1e-10)

        # 成交量变化率（相对于10日均量）
        vol_ma10 = volume.rolling(10).mean()
        vol_ratio = volume / vol_ma10

        # 突破判断：价格接近最近高点或低点（距离<2%）
        near_high = (close >= recent_high * 0.98)
        near_low = (close <= recent_low * 1.02)

        # 信号：突破但成交量萎缩->负值；突破且放量->正值
        # 使用价格位置加权：在极值时信号强度大
        signal = np.where(
            near_high,
            (vol_ratio - 1.0) * (price_pos - 0.5) * 2,
            np.where(
                near_low,
                (vol_ratio - 1.0) * (0.5 - price_pos) * 2,
                0.0
            )
        )
        # 平滑与归一化
        signal = pd.Series(signal, index=close.index).rolling(3).mean()
        norm_signal = signal / (signal.abs().rolling(50).mean() + 0.1)
        result = norm_signal.clip(-1, 1)
        return result.fillna(0)
