"""AI因子: 插针影线不对称反转 | 置信:62% | 计算下影线与上影线相对实体的大小差异（不对称度），正值代表买方防守（锤子线），负值代表卖方拒绝（射击之星）。对不对称度做3期平滑后与短期收益方向交互，捕捉插针后的均值回归alpha：下影主导后短期反弹概率高，上影主导后回落概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinbarSkewReversal(BaseFactor):
    """计算下影线与上影线相对实体的大小差异（不对称度），正值代表买方防守（锤子线），负值代表卖方拒绝（射击之星）。对不对称度做3期平滑后与短期收益方向交互，捕捉插针后的均值回归alpha：下影主导后短期反弹概率高，上影主导后回落概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_skew_rev",
            name="Pinbar Skew Reversal",
            display_name="插针影线不对称反转",
            description="计算下影线与上影线相对实体的大小差异（不对称度），正值代表买方防守（锤子线），负值代表卖方拒绝（射击之星）。对不对称度做3期平滑后与短期收益方向交互，捕捉插针后的均值回归alpha：下影主导后短期反弹概率高，上影主导后回落概率高。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        skew = (lower - upper) / body
        ret = data['close'].pct_change(3)
        result = (skew.rolling(3).mean() * (-ret)).clip(-1, 1)
        return result
