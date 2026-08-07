"""AI因子: 布林带噪声区 | 置信:55% | 价格处于布林带中轨附近（带宽窄且价格偏离幅度小）同时成交量萎缩，表明市场缺乏突破动力，容易触发连续小额止损。该因子在此区域输出负信号，指示不应做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Noise_Zone(BaseFactor):
    """价格处于布林带中轨附近（带宽窄且价格偏离幅度小）同时成交量萎缩，表明市场缺乏突破动力，容易触发连续小额止损。该因子在此区域输出负信号，指示不应做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbzone",
            name="Bollinger_Band_Noise_Zone",
            display_name="布林带噪声区",
            description="价格处于布林带中轨附近（带宽窄且价格偏离幅度小）同时成交量萎缩，表明市场缺乏突破动力，容易触发连续小额止损。该因子在此区域输出负信号，指示不应做多。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算布林带 (20,2)
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        bandwidth = (upper - lower) / sma
        # 价格相对中轨位置 (0~1)
        position = (close - lower) / (upper - lower)
        # 窄带且价格靠近中轨 (0.4~0.6)
        narrow_band = (bandwidth < bandwidth.rolling(50).mean() * 0.8)
        mid_zone = (position > 0.4) & (position < 0.6)
        # 成交量萎缩 (相对20日均量低20%)
        vol_ma = volume.rolling(20).mean()
        low_vol = volume < vol_ma * 0.8
        # 综合信号
        noise = (narrow_band & mid_zone & low_vol).astype(int)
        # 映射到-1
        result = -noise.rolling(3).max().fillna(0)
        return pd.Series(result, index=data.index).clip(-1, 0)
