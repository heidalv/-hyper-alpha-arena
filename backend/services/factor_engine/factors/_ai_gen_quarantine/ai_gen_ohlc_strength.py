"""AI因子: 日内多空强度 | 置信:70% | 基于开盘、最高、最低、收盘的相对位置判断日内多空力量对比。公式： (close - open) / (high - low) * (high - low) / atr 并结合成交量确认。输出范围[-1,1]，正值表示多头占优但过滤掉极端情况。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Intraday_OHLC_Strength(BaseFactor):
    """基于开盘、最高、最低、收盘的相对位置判断日内多空力量对比。公式： (close - open) / (high - low) * (high - low) / atr 并结合成交量确认。输出范围[-1,1]，正值表示多头占优但过滤掉极端情况。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ohlc_strength",
            name="Intraday OHLC Strength",
            display_name="日内多空强度",
            description="基于开盘、最高、最低、收盘的相对位置判断日内多空力量对比。公式： (close - open) / (high - low) * (high - low) / atr 并结合成交量确认。输出范围[-1,1]，正值表示多头占优但过滤掉极端情况。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        open_ = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        range_ = high - low
        close_open = close - open_
        # 防止除以零
        pos = close_open / (range_ + 1e-10)
        # 乘以相对波动率权重: 用ATR平滑
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        weight = range_ / (atr + 1e-10)
        raw = pos * weight
        # 成交量调整: 异常量大则强化信号
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        raw = raw * vol_ratio.clip(0.5, 2)
        # 归一化到[-1,1]
        norm = (raw - raw.rolling(60).mean()) / raw.rolling(60).std()
        return norm.clip(-3, 3) / 3.0
