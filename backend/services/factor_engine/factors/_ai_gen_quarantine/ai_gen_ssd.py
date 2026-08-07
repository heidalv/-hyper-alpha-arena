"""AI因子: 短线震荡衰减因子 | 置信:60% | 基于价格在短期均线上下穿越的频率和幅度，判断市场是否处于无趋势震荡状态。频繁的穿越伴随递减的幅度预示即将到来的方向选择，容易导致止损或超时亏损。因子值正表示震荡衰减后可能向下突破，负表示向上突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSwingDecay(BaseFactor):
    """基于价格在短期均线上下穿越的频率和幅度，判断市场是否处于无趋势震荡状态。频繁的穿越伴随递减的幅度预示即将到来的方向选择，容易导致止损或超时亏损。因子值正表示震荡衰减后可能向下突破，负表示向上突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ssd",
            name="Short_Swing_Decay",
            display_name="短线震荡衰减因子",
            description="基于价格在短期均线上下穿越的频率和幅度，判断市场是否处于无趋势震荡状态。频繁的穿越伴随递减的幅度预示即将到来的方向选择，容易导致止损或超时亏损。因子值正表示震荡衰减后可能向下突破，负表示向上突破。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        short_ma = close.rolling(5).mean()
        long_ma = close.rolling(20).mean()
        # 计算短期偏离度
        dev = (close - short_ma) / (short_ma + 1e-10)
        # 计算穿越次数（最近5根K线中穿过均线的次数）
        cross = ((close.shift(1) <= short_ma.shift(1)) & (close > short_ma)) | \
                ((close.shift(1) >= short_ma.shift(1)) & (close < short_ma))
        cross_count = cross.rolling(5).sum()
        # 幅度衰减：最近一次偏离的绝对值 vs 前一次
        abs_dev = dev.abs()
        decay = abs_dev - abs_dev.shift(1)
        # 组合信号：高穿越次数 + 衰减幅度 => 震荡衰减
        ssd = cross_count * (1 - decay.clip(0, 1))  # 正值表示衰减
        # 归一化
        result = (ssd - ssd.rolling(20).mean()) / (ssd.rolling(20).std() + 1e-10)
        return result.clip(-1, 1)
