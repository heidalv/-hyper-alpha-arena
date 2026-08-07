"""AI因子: 价量比率 | 置信:60% | 计算价格变化百分比与成交量变化百分比的比率，衡量单位成交量驱动的价格效率。高正值表示价升量缩（异常上涨），低负值表示价跌量缩（异常下跌）。通过归一化到[-1,1]区间，用于识别反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Volume_Ratio(BaseFactor):
    """计算价格变化百分比与成交量变化百分比的比率，衡量单位成交量驱动的价格效率。高正值表示价升量缩（异常上涨），低负值表示价跌量缩（异常下跌）。通过归一化到[-1,1]区间，用于识别反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pvr",
            name="Price-Volume Ratio",
            display_name="价量比率",
            description="计算价格变化百分比与成交量变化百分比的比率，衡量单位成交量驱动的价格效率。高正值表示价升量缩（异常上涨），低负值表示价跌量缩（异常下跌）。通过归一化到[-1,1]区间，用于识别反转信号。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        price_chg = close.pct_change()
        vol_chg = volume.pct_change()
        # 避免除零
        ratio = price_chg / (vol_chg + 1e-10)
        # 滚动标准化到[-1,1]
        roll_mean = ratio.rolling(20).mean()
        roll_std = ratio.rolling(20).std().replace(0, 1e-10)
        z_score = (ratio - roll_mean) / roll_std
        result = z_score.clip(-3, 3) / 3.0
        return result.fillna(0.0)
