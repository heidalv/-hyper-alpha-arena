"""AI因子: 插针影线不对称反转 | 置信:62% | 计算K线下影线与上影线的不对称度(lower-upper)/body，反映买方防守vs卖方拒绝的强弱。取3周期滚动均值平滑噪音，正值代表下影主导(承接强，后续反弹概率高)，负值代表上影主导(抛压强，后续回落概率高)。短线插针反转alpha，直接预测未来收益方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算K线下影线与上影线的不对称度(lower-upper)/body，反映买方防守vs卖方拒绝的强弱。取3周期滚动均值平滑噪音，正值代表下影主导(承接强，后续反弹概率高)，负值代表上影主导(抛压强，后续回落概率高)。短线插针反转alpha，直接预测未来收益方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_v1",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算K线下影线与上影线的不对称度(lower-upper)/body，反映买方防守vs卖方拒绝的强弱。取3周期滚动均值平滑噪音，正值代表下影主导(承接强，后续反弹概率高)，负值代表上影主导(抛压强，后续回落概率高)。短线插针反转alpha，直接预测未来收益方向。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
