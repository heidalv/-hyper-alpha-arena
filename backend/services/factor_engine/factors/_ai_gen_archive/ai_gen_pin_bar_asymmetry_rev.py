"""AI因子: 插针影线不对称反转 | 置信:62% | 计算下影线与上影线的不对称度（(lower-upper)/body），正值表示买方防守强（锤子线特征），负值表示卖方拒绝强（射击之星特征）。对该不对称度做3周期滚动均值平滑后取反，捕捉插针后的短期均值回归：长下影承接后倾向反弹、长上影后倾向回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算下影线与上影线的不对称度（(lower-upper)/body），正值表示买方防守强（锤子线特征），负值表示卖方拒绝强（射击之星特征）。对该不对称度做3周期滚动均值平滑后取反，捕捉插针后的短期均值回归：长下影承接后倾向反弹、长上影后倾向回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asymmetry_rev",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算下影线与上影线的不对称度（(lower-upper)/body），正值表示买方防守强（锤子线特征），负值表示卖方拒绝强（射击之星特征）。对该不对称度做3周期滚动均值平滑后取反，捕捉插针后的短期均值回归：长下影承接后倾向反弹、长上影后倾向回落。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        result = (-asym).rolling(3).mean().clip(-1, 1)
        return result
