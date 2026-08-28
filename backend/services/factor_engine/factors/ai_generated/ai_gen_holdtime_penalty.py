"""AI因子: 持仓超时惩罚因子 | 置信:65% | 针对max_hold_timeout亏损，检测价格在窄幅区间内长时间横盘后可能出现的趋势衰竭。用ATR相对价格位置和波动率收缩度衡量，值越高表示横盘越久且波动越萎缩，预示持仓超时风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holdtimepenalty(BaseFactor):
    """针对max_hold_timeout亏损，检测价格在窄幅区间内长时间横盘后可能出现的趋势衰竭。用ATR相对价格位置和波动率收缩度衡量，值越高表示横盘越久且波动越萎缩，预示持仓超时风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtime_penalty",
            name="HoldTimePenalty",
            display_name="持仓超时惩罚因子",
            description="针对max_hold_timeout亏损，检测价格在窄幅区间内长时间横盘后可能出现的趋势衰竭。用ATR相对价格位置和波动率收缩度衡量，值越高表示横盘越久且波动越萎缩，预示持仓超时风险。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        atr = (high - low).rolling(14).mean()
        range_pct = (high.rolling(20).max() - low.rolling(20).min()) / (close.rolling(20).mean() + 1e-9)
        vol_ratio = atr / (close.rolling(20).std() + 1e-9)
        result = (range_pct - vol_ratio).clip(-1, 1)
        return result
