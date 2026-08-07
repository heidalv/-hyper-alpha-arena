"""AI因子: 逆向动量 | 置信:45% | 通过价格变动与成交量背离识别短期趋势衰竭反转。当价格快速上涨但成交量萎缩，或价格快速下跌但成交量放大时，发出反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseMomentum(BaseFactor):
    """通过价格变动与成交量背离识别短期趋势衰竭反转。当价格快速上涨但成交量萎缩，或价格快速下跌但成交量放大时，发出反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_mom",
            name="Reverse Momentum",
            display_name="逆向动量",
            description="通过价格变动与成交量背离识别短期趋势衰竭反转。当价格快速上涨但成交量萎缩，或价格快速下跌但成交量放大时，发出反向信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算短期价格变化率
        price_change = data['close'].pct_change(2)
        # 计算成交量变化率（平滑处理）
        vol_change = data['volume'].pct_change(2).fillna(0)
        # 价格与成交量背离指标：价格上涨时成交量下降为正背离，反之为负背离
        divergence = price_change * (vol_change * (-1))
        # 标准化到[-1,1]区间，使用tanh压缩
        result = np.tanh(divergence * 10)
        return result.fillna(0)
