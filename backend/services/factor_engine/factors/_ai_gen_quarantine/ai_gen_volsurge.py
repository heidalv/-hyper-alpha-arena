"""AI因子: 量价背离 | 置信:55% | 检测成交量异常放大但价格未同步上涨（或下跌）的情况，识别潜在反转或假突破，避免在放量陷阱中开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeSurgeDivergence(BaseFactor):
    """检测成交量异常放大但价格未同步上涨（或下跌）的情况，识别潜在反转或假突破，避免在放量陷阱中开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volsurge",
            name="Volume Surge Divergence",
            display_name="量价背离",
            description="检测成交量异常放大但价格未同步上涨（或下跌）的情况，识别潜在反转或假突破，避免在放量陷阱中开仓。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        volume = data['volume']
        close = data['close']
        # 成交量相对历史均值
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        # 价格变化率
        price_change = close.pct_change(5)
        # 量增而价不涨或跌：负相关
        # 构造信号：当成交量异常大（>1.5倍均值）且价格变化很小（|price_change|<0.02）时，发出负信号
        # 否则根据量价同步给出轻微信号
        surge = (vol_ratio > 1.5).astype(float)
        small_move = (np.abs(price_change) < 0.02).astype(float)
        # 合成：量缩时中性，量增价不动则-0.8，量增价动则正信号但较弱
        result = np.where(
            surge & small_move, -0.8,
            np.where(
                surge & (price_change > 0.02), 0.3,
                np.where(
                    surge & (price_change < -0.02), -0.3,
                    0.0
                )
            )
        )
        return pd.Series(result, index=data.index)
