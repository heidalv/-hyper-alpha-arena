"""AI因子: 插针影线不对称反转 | 置信:62% | 计算上下影线不对称度(lower-upper)/body，正值代表下影主导(买方防守)，负值代表上影主导(卖方拒绝)。取3日滚动均值后与短期5日收益方向反向交互：当价格短期下跌但出现下影承接时因子转正，预示反弹；短期上涨但上影拒绝时因子转负，预示回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal5D(BaseFactor):
    """计算上下影线不对称度(lower-upper)/body，正值代表下影主导(买方防守)，负值代表上影主导(卖方拒绝)。取3日滚动均值后与短期5日收益方向反向交互：当价格短期下跌但出现下影承接时因子转正，预示反弹；短期上涨但上影拒绝时因子转负，预示回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_5d",
            name="Pin Bar Asymmetry Reversal 5D",
            display_name="插针影线不对称反转",
            description="计算上下影线不对称度(lower-upper)/body，正值代表下影主导(买方防守)，负值代表上影主导(卖方拒绝)。取3日滚动均值后与短期5日收益方向反向交互：当价格短期下跌但出现下影承接时因子转正，预示反弹；短期上涨但上影拒绝时因子转负，预示回落。",
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
        ret = data['close'].pct_change(5)
        result = (asym - ret * 10).clip(-1, 1)
        return result
