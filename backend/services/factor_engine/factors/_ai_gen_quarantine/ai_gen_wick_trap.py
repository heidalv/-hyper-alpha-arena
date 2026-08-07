"""AI因子: 影线陷阱反转 | 置信:55% | 根据K线上影线和下影线长度与实体长度的比值，识别可能形成顶部或底部反转的陷阱形态。上影线/(上影线+实体) > 0.7且成交量高于过去10日均值1.5倍时，生成负向信号（看空）；下影线/(下影线+实体) > 0.7且成交量放大时，生成正向信号（看多）。输出连续值[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickTrapReversal(BaseFactor):
    """根据K线上影线和下影线长度与实体长度的比值，识别可能形成顶部或底部反转的陷阱形态。上影线/(上影线+实体) > 0.7且成交量高于过去10日均值1.5倍时，生成负向信号（看空）；下影线/(下影线+实体) > 0.7且成交量放大时，生成正向信号（看多）。输出连续值[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_trap",
            name="Wick Trap Reversal",
            display_name="影线陷阱反转",
            description="根据K线上影线和下影线长度与实体长度的比值，识别可能形成顶部或底部反转的陷阱形态。上影线/(上影线+实体) > 0.7且成交量高于过去10日均值1.5倍时，生成负向信号（看空）；下影线/(下影线+实体) > 0.7且成交量放大时，生成正向信号（看多）。输出连续值[-1,1]。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        open = data['open']
        close = data['close']
        volume = data['volume']
        # 实体和影线
        body = np.abs(close - open)
        upper_wick = high - np.maximum(open, close)
        lower_wick = np.minimum(open, close) - low
        # 避免除以零
        body_safe = np.where(body == 0, 1e-8, body)
        upper_ratio = upper_wick / (upper_wick + body)
        lower_ratio = lower_wick / (lower_wick + body)
        # 成交量条件
        vol_ma = volume.rolling(10).mean()
        vol_surge = volume / vol_ma
        # 信号组合：上影线陷阱为空，下影线陷阱为多
        short_signal = -1.0 * (upper_ratio > 0.7) * (vol_surge > 1.5)
        long_signal = 1.0 * (lower_ratio > 0.7) * (vol_surge > 1.5)
        signal = long_signal + short_signal
        # 平滑处理，避免跳变
        signal = signal.rolling(3).mean()
        signal = np.clip(signal, -1, 1)
        return signal
