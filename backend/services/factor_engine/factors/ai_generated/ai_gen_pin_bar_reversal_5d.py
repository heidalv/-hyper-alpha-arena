"""AI因子: 插针影线不对称反转 | 置信:62% | 计算K线上下影线不对称度(lower-upper)/body，正值代表下影主导(买方防守)，负值代表上影主导(卖方拒绝)。取3日滚动均值后与5日收益方向交互：当近期下跌且出现下影承接时给出正向信号(反弹)，当近期上涨且出现上影拒绝时给出负向信号(回落)。属于短线插针反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal5D(BaseFactor):
    """计算K线上下影线不对称度(lower-upper)/body，正值代表下影主导(买方防守)，负值代表上影主导(卖方拒绝)。取3日滚动均值后与5日收益方向交互：当近期下跌且出现下影承接时给出正向信号(反弹)，当近期上涨且出现上影拒绝时给出负向信号(回落)。属于短线插针反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_5d",
            name="Pin Bar Asymmetry Reversal 5D",
            display_name="插针影线不对称反转",
            description="计算K线上下影线不对称度(lower-upper)/body，正值代表下影主导(买方防守)，负值代表上影主导(卖方拒绝)。取3日滚动均值后与5日收益方向交互：当近期下跌且出现下影承接时给出正向信号(反弹)，当近期上涨且出现上影拒绝时给出负向信号(回落)。属于短线插针反转alpha。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = ((lower - upper) / body).rolling(3).mean()
        ret5 = data['close'].pct_change(5)
        result = (asym - ret5 * 8).clip(-1, 1)
        return result
