"""AI因子: 成交量停滞陷阱检测 | 置信:60% | 识别成交量萎缩且价格波动率极低的状态，这种状态常出现在行情突破前或流动性陷阱中，容易导致止损和超时亏损。计算成交量相对20日均值的偏离（负值表示萎缩）与价格范围（high-low）/close的比值，当两者同时走低时产生负信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Stagnation_Trap_Detector(BaseFactor):
    """识别成交量萎缩且价格波动率极低的状态，这种状态常出现在行情突破前或流动性陷阱中，容易导致止损和超时亏损。计算成交量相对20日均值的偏离（负值表示萎缩）与价格范围（high-low）/close的比值，当两者同时走低时产生负信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_stall",
            name="Volume Stagnation Trap Detector",
            display_name="成交量停滞陷阱检测",
            description="识别成交量萎缩且价格波动率极低的状态，这种状态常出现在行情突破前或流动性陷阱中，容易导致止损和超时亏损。计算成交量相对20日均值的偏离（负值表示萎缩）与价格范围（high-low）/close的比值，当两者同时走低时产生负信号。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame
        volume = data['volume']
        price_range = (data['high'] - data['low']) / data['close']
        # 成交量相对20日均值
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20 - 1  # 偏离度
        # 价格波动率相对20日均值
        range_ma20 = price_range.rolling(20).mean()
        range_ratio = price_range / range_ma20 - 1
        # 当两者都低于0时，表示萎缩和低波动
        stall = (vol_ratio < -0.3) & (range_ratio < -0.3)
        score = np.where(stall, -1.0, 1.0)
        series = pd.Series(score, index=data.index).rolling(5).mean().fillna(0)
        return series.clip(-1, 1)
