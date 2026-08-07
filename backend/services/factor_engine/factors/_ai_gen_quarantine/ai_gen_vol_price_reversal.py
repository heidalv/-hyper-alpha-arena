"""AI因子: 成交量确认极端价格反转 | 置信:60% | 当价格单日涨跌超过近期（20日）均值2倍标准差，且成交量放大至均量2倍以上时，认为极端情绪导致过度反应，后续大概率反转。此因子针对dust_cleanup和sl模式中追涨杀跌的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmedExtremePriceReversal(BaseFactor):
    """当价格单日涨跌超过近期（20日）均值2倍标准差，且成交量放大至均量2倍以上时，认为极端情绪导致过度反应，后续大概率反转。此因子针对dust_cleanup和sl模式中追涨杀跌的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_reversal",
            name="Volume Confirmed Extreme Price Reversal",
            display_name="成交量确认极端价格反转",
            description="当价格单日涨跌超过近期（20日）均值2倍标准差，且成交量放大至均量2倍以上时，认为极端情绪导致过度反应，后续大概率反转。此因子针对dust_cleanup和sl模式中追涨杀跌的亏损。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 日收益率
        ret = close.pct_change()
        # 滚动20日均值和标准差
        ret_mean = ret.rolling(20).mean()
        ret_std = ret.rolling(20).std()
        # 价格极端: 超过2倍标准差
        extreme_up = (ret > ret_mean + 2 * ret_std) & (ret > 0.03)  # 同时确保绝对涨幅>3%
        extreme_down = (ret < ret_mean - 2 * ret_std) & (ret < -0.03)
        # 成交量异常: 为20日均量的2倍以上
        vol_ma = volume.rolling(20).mean()
        vol_surge = volume > (2 * vol_ma)
        # 信号：极端且放量 -> 预期反转
        signal = np.where(extreme_up & vol_surge, -1.0, np.where(extreme_down & vol_surge, 1.0, 0.0))
        return pd.Series(signal, index=data.index).fillna(0)
