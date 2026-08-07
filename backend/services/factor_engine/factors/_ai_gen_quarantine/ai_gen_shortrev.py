"""AI因子: 短期反转强度 | 置信:55% | 基于经典识别近期极端走势后反转的因子。使用过去几根K线的收盘价相对位置，结合成交量确认，当价格快速上涨但成交量萎缩时预示回调，反之预示反弹。该因子捕捉了master_running和max_hold_timeout中常见的假突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Short_Term_Reversal_Strength(BaseFactor):
    """基于经典识别近期极端走势后反转的因子。使用过去几根K线的收盘价相对位置，结合成交量确认，当价格快速上涨但成交量萎缩时预示回调，反之预示反弹。该因子捕捉了master_running和max_hold_timeout中常见的假突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_shortrev",
            name="Short_Term_Reversal_Strength",
            display_name="短期反转强度",
            description="基于经典识别近期极端走势后反转的因子。使用过去几根K线的收盘价相对位置，结合成交量确认，当价格快速上涨但成交量萎缩时预示回调，反之预示反弹。该因子捕捉了master_running和max_hold_timeout中常见的假突破。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        period = 5
        close = data['close']
        volume = data['volume']
        # 价格相对位置: (close - min) / (max - min)
        low = close.rolling(period).min()
        high = close.rolling(period).max()
        pos = (close - low) / (high - low + 1e-8)
        # 成交量相对变化: 当前成交量 / 过去均值
        vol_ma = volume.rolling(period).mean().replace(0, 1e-8)
        vol_ratio = volume / vol_ma
        # 反转信号: 高位且缩量 => 看跌(-1); 低位且放量 => 看涨(+1)
        # 使用 sigmoid-like 映射
        signal = (0.5 - pos) * (vol_ratio - 1)
        result = signal.clip(-2, 2) / 2.0
        return result.fillna(0)
