"""AI因子: 持仓超时惩罚 | 置信:70% | 基于亏损模式中max_hold_timeout高频出现，构建一个惩罚长时间持仓的因子。当价格长期横盘（波动率低）且持仓时间过长时，给予负向信号。使用ATR归一化的价格变化和波动率衰减来识别横盘状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutPenalty(BaseFactor):
    """基于亏损模式中max_hold_timeout高频出现，构建一个惩罚长时间持仓的因子。当价格长期横盘（波动率低）且持仓时间过长时，给予负向信号。使用ATR归一化的价格变化和波动率衰减来识别横盘状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_timeout_penalty",
            name="Hold Timeout Penalty",
            display_name="持仓超时惩罚",
            description="基于亏损模式中max_hold_timeout高频出现，构建一个惩罚长时间持仓的因子。当价格长期横盘（波动率低）且持仓时间过长时，给予负向信号。使用ATR归一化的价格变化和波动率衰减来识别横盘状态。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        atr = (data['high'] - data['low']).rolling(10).mean()
        norm_ret = ret / (atr / (data['close'] + 1e-9) + 1e-9)
        vol_ratio = data['close'].pct_change().rolling(5).std() / (data['close'].pct_change().rolling(20).std() + 1e-9)
        result = (-norm_ret * (1 - vol_ratio)).clip(-1, 1)
        return result
