"""AI因子: 流动性磁铁反转 | 置信:60% | 捕捉价格快速下跌后伴随成交量放大的反弹，识别空头陷阱。计算最近5根K线内价格从最低点反弹的幅度，并乘以成交量放大比率，再映射到[-1,1]。正值表示反转做多信号，负值表示继续下跌做空信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """捕捉价格快速下跌后伴随成交量放大的反弹，识别空头陷阱。计算最近5根K线内价格从最低点反弹的幅度，并乘以成交量放大比率，再映射到[-1,1]。正值表示反转做多信号，负值表示继续下跌做空信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_mag",
            name="Liquidity Magnet Reversal",
            display_name="流动性磁铁反转",
            description="捕捉价格快速下跌后伴随成交量放大的反弹，识别空头陷阱。计算最近5根K线内价格从最低点反弹的幅度，并乘以成交量放大比率，再映射到[-1,1]。正值表示反转做多信号，负值表示继续下跌做空信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        lookback = 5
        low_min = data['low'].rolling(lookback).min()
        high_max = data['high'].rolling(lookback).max()
        # 反弹比例：当前close相对于低点的涨幅，除以过去高点-低点范围
        bounce = (data['close'] - low_min) / (high_max - low_min + 1e-10)
        # 成交量放大：当前volume相对于过去5期均值的比率
        vol_ma = data['volume'].rolling(lookback).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        # 综合得分：反弹越大且成交量越大得分越高
        raw = bounce * np.log1p(vol_ratio)
        # 标准化到[-1,1]，用tanh压缩
        result = np.tanh(raw * 3 - 1.5)
        return result
