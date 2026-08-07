"""AI因子: 近期高点的回撤与成交量激增 | 置信:55% | 计算当前价格相对于过去10日高点的回撤比例，并与过去20日平均成交量比较。当回撤较大且成交量显著高于均值时，表明可能出现反转或止损踩踏，输出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceRetracementFromRecentHighWithVolumeSurge(BaseFactor):
    """计算当前价格相对于过去10日高点的回撤比例，并与过去20日平均成交量比较。当回撤较大且成交量显著高于均值时，表明可能出现反转或止损踩踏，输出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_retrace",
            name="Price Retracement from Recent High with Volume Surge",
            display_name="近期高点的回撤与成交量激增",
            description="计算当前价格相对于过去10日高点的回撤比例，并与过去20日平均成交量比较。当回撤较大且成交量显著高于均值时，表明可能出现反转或止损踩踏，输出负向信号。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        volume = data['volume']
        recent_high = high.rolling(10).max()
        retrace = (recent_high - close) / recent_high
        vol_ratio = volume / volume.rolling(20).mean()
        # 当回撤>2%且成交量>2倍均值时，产生负向信号
        signal = -np.where((retrace > 0.02) & (vol_ratio > 2.0), retrace * 2, 0)
        # 限制在[-1,1]并平滑
        signal = np.clip(signal, -1, 1)
        return pd.Series(signal, index=data.index).fillna(0)
