"""AI因子: 波动率挤压衰竭因子 | 置信:68% | 识别布林带宽度收缩至近期低位且价格处于上轨附近的假突破风险，避免在波动率扩张失败时追多导致max_hold_timeout亏损。负值表示挤压衰竭风险高，不宜做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueezeFailurePredictor(BaseFactor):
    """识别布林带宽度收缩至近期低位且价格处于上轨附近的假突破风险，避免在波动率扩张失败时追多导致max_hold_timeout亏损。负值表示挤压衰竭风险高，不宜做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vs",
            name="Volatility Squeeze Failure Predictor",
            display_name="波动率挤压衰竭因子",
            description="识别布林带宽度收缩至近期低位且价格处于上轨附近的假突破风险，避免在波动率扩张失败时追多导致max_hold_timeout亏损。负值表示挤压衰竭风险高，不宜做多。",
            category="volatility",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 布林带
        mid = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = mid + 2*std
        lower = mid - 2*std
        bb_width = (upper - lower) / mid.replace(0, 1e-9)
        # 带宽处于20日低位
        bw_low = bb_width.rolling(20).rank(pct=True) < 0.2
        # 价格靠近上轨
        price_near_upper = (close - mid) / (upper - mid + 1e-9) > 0.8
        # 挤压衰竭风险
        squeeze_fail = bw_low & price_near_upper
        # 动量衰减：近期价格变动缩窄
        roc = close.pct_change(5)
        mom_decay = roc.rolling(5).std() < roc.rolling(20).std() * 0.5
        # 综合风险分数
        risk = (squeeze_fail.astype(float) * 0.6 + mom_decay.astype(float) * 0.4) * -1
        # 否则中性
        result = risk.clip(-1, 1).fillna(0)
        return result
