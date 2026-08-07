"""AI因子: 量价背离风险 | 置信:55% | 检测价格变动与成交量变动是否一致。当价格上涨但成交量萎缩，或价格下跌但成交量放大（多头陷阱），因子输出负值，提示做多风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence_Risk(BaseFactor):
    """检测价格变动与成交量变动是否一致。当价格上涨但成交量萎缩，或价格下跌但成交量放大（多头陷阱），因子输出负值，提示做多风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_clash",
            name="Volume-Price Divergence Risk",
            display_name="量价背离风险",
            description="检测价格变动与成交量变动是否一致。当价格上涨但成交量萎缩，或价格下跌但成交量放大（多头陷阱），因子输出负值，提示做多风险。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ret = close.pct_change()
        vol_change = volume.pct_change()
        # 价格涨时量缩为负信号，价格跌时量增也为负信号
        # 使用乘积：ret * vol_change，若同方向（涨量增或跌量缩）为正，反之为负
        # 但需要标准化到[-1,1]
        product = ret * vol_change
        # 用tanh限制幅度
        result = -np.tanh(product * 10)  # 负值表示背离风险
        return result.fillna(0)
