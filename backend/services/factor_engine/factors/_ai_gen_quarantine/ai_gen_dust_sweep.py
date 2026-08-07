"""AI因子: 灰尘清扫反转 | 置信:50% | 识别小币种经过长时间阴跌后突然出现大单抛售但价格未创新低，随后可能被主力清扫。通过计算价格相对20日低点的距离、成交量异常（单根K线成交量超过20日均值的2倍）以及价格窄幅波动后的突破方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupReversal(BaseFactor):
    """识别小币种经过长时间阴跌后突然出现大单抛售但价格未创新低，随后可能被主力清扫。通过计算价格相对20日低点的距离、成交量异常（单根K线成交量超过20日均值的2倍）以及价格窄幅波动后的突破方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_sweep",
            name="Dust Cleanup Reversal",
            display_name="灰尘清扫反转",
            description="识别小币种经过长时间阴跌后突然出现大单抛售但价格未创新低，随后可能被主力清扫。通过计算价格相对20日低点的距离、成交量异常（单根K线成交量超过20日均值的2倍）以及价格窄幅波动后的突破方向。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 20日最低价
        low_20 = data['low'].rolling(20).min()
        # 当前价格距20日低点百分比
        price_from_low = (data['close'] - low_20) / low_20
        # 成交量均值
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma20
        # 条件：价格处于20日低点附近（<1%），成交量突然放大（>2倍），且最近3根K线波动率小（ATR/close <0.02）
        atr = (data['high'] - data['low']).rolling(14).mean()
        low_vol = atr / data['close'] < 0.02
        cond = (price_from_low < 0.01) & (vol_ratio > 2.0) & low_vol
        # 输出信号：若满足条件且价格从低点反弹，看多；否则中性
        factor = cond.astype(float) * np.sign(data['close'] - data['close'].shift(1))
        return factor
