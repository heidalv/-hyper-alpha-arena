"""AI因子: 插针影线不对称反转 | 置信:62% | 计算下影线与上影线相对实体的大小差异，衡量买卖双方在极值处的防守强度。下影主导（买方承接）预示短期反弹，上影主导（卖方拒绝）预示短期回落。对不对称度做短期平滑后取负号与短期收益方向结合，捕捉插针后的均值回归 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算下影线与上影线相对实体的大小差异，衡量买卖双方在极值处的防守强度。下影主导（买方承接）预示短期反弹，上影主导（卖方拒绝）预示短期回落。对不对称度做短期平滑后取负号与短期收益方向结合，捕捉插针后的均值回归 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_short",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算下影线与上影线相对实体的大小差异，衡量买卖双方在极值处的防守强度。下影主导（买方承接）预示短期反弹，上影主导（卖方拒绝）预示短期回落。对不对称度做短期平滑后取负号与短期收益方向结合，捕捉插针后的均值回归 alpha。",
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
        smooth = asym.rolling(3).mean()
        ret = data['close'].pct_change(3)
        result = (smooth - ret).clip(-1, 1)
        return result
