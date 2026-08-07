"""AI因子: 波动调整均值回复强度 | 置信:70% | 计算价格在布林带内的相对位置，结合近期波动率变化（标准差比），当波动率急剧收缩且价格靠近上轨时，预示假突破可能，输出负向信号；反之靠近下轨且波动率扩张时，可能超跌反弹，输出正向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Reversion_Indicator(BaseFactor):
    """计算价格在布林带内的相对位置，结合近期波动率变化（标准差比），当波动率急剧收缩且价格靠近上轨时，预示假突破可能，输出负向信号；反之靠近下轨且波动率扩张时，可能超跌反弹，输出正向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_rv",
            name="Volatility_Adjusted_Reversion_Indicator",
            display_name="波动调整均值回复强度",
            description="计算价格在布林带内的相对位置，结合近期波动率变化（标准差比），当波动率急剧收缩且价格靠近上轨时，预示假突破可能，输出负向信号；反之靠近下轨且波动率扩张时，可能超跌反弹，输出正向信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算典型价格
        tp = (data['high'] + data['low'] + data['close']) / 3
        # 布林带 (20周期)
        window = 20
        sma = tp.rolling(window).mean()
        std = tp.rolling(window).std()
        upper = sma + 2*std
        lower = sma - 2*std
        # 价格在布林带内的位置 (0~1)
        bb_pos = (data['close'] - lower) / (upper - lower).replace(0, np.nan)
        # 波动率变化：近5日标准差 / 近20日标准差
        vol_short = tp.rolling(5).std()
        vol_long = tp.rolling(20).std()
        vol_ratio = vol_short / vol_long.replace(0, np.nan)
        # 综合信号：当位置接近上轨(>0.8)且波动率收缩(ratio<0.5)时，看跌(-1)；接近下轨(<0.2)且波动率扩张(ratio>2)时，看涨(+1)
        signal = np.where((bb_pos > 0.8) & (vol_ratio < 0.5), -1,
                         np.where((bb_pos < 0.2) & (vol_ratio > 2.0), 1, 0))
        # 平滑处理，避免频繁切换
        result = pd.Series(signal, index=data.index).rolling(3).mean().fillna(0)
        return result.clip(-1, 1)
