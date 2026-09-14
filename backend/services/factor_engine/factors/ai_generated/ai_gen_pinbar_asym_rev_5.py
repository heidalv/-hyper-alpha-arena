"""AI因子: 插针影线不对称反转 | 置信:62% | 计算下影线与上影线的不对称度（(lower-upper)/body），正值代表买方防守（锤子线特征），负值代表卖方拒绝（射击之星特征）。取3日滚动均值后与5日短期收益方向做反向交互，捕捉插针后的均值回归alpha：下影主导后倾向反弹、上影主导后倾向回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal5(BaseFactor):
    """计算下影线与上影线的不对称度（(lower-upper)/body），正值代表买方防守（锤子线特征），负值代表卖方拒绝（射击之星特征）。取3日滚动均值后与5日短期收益方向做反向交互，捕捉插针后的均值回归alpha：下影主导后倾向反弹、上影主导后倾向回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_asym_rev_5",
            name="Pin Bar Asymmetry Reversal 5",
            display_name="插针影线不对称反转",
            description="计算下影线与上影线的不对称度（(lower-upper)/body），正值代表买方防守（锤子线特征），负值代表卖方拒绝（射击之星特征）。取3日滚动均值后与5日短期收益方向做反向交互，捕捉插针后的均值回归alpha：下影主导后倾向反弹、上影主导后倾向回落。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = ((lower - upper) / body).rolling(3).mean()
        ret5 = data['close'].pct_change(5)
        result = (asym - ret5 * 3.0).clip(-1, 1)
        return result
