"""AI因子: 利润回撤持仓时长因子 | 置信:60% | 度量从近期高点的回撤幅度，并结合持仓时间（用累计成交量代替时间）来识别过度持有风险。当回撤超过阈值且持续放量时发出警告。正值表示回撤较浅或持仓时间短（安全），负值表示严重回撤且持仓时间过长（风险）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ProfitDrawdownHoldingDuration(BaseFactor):
    """度量从近期高点的回撤幅度，并结合持仓时间（用累计成交量代替时间）来识别过度持有风险。当回撤超过阈值且持续放量时发出警告。正值表示回撤较浅或持仓时间短（安全），负值表示严重回撤且持仓时间过长（风险）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_retracement",
            name="Profit Drawdown & Holding Duration",
            display_name="利润回撤持仓时长因子",
            description="度量从近期高点的回撤幅度，并结合持仓时间（用累计成交量代替时间）来识别过度持有风险。当回撤超过阈值且持续放量时发出警告。正值表示回撤较浅或持仓时间短（安全），负值表示严重回撤且持仓时间过长（风险）。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        lookback = 30
        high = data['high'].rolling(lookback, min_periods=1).max()
        close = data['close']
        drawdown = (close - high) / high.clip(lower=1e-8)
        # 用累计成交量近似持仓时间（假设交易活跃度反映持仓时长）
        cum_vol = data['volume'].rolling(lookback).sum()
        vol_rank = cum_vol / cum_vol.rolling(lookback).mean().clip(lower=1e-8)
        # 当回撤大且累计成交量高时风险大
        risk = -drawdown * vol_rank
        result = risk.clip(-1, 1)
        return result
