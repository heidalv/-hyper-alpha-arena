"""AI因子: 波动率状态突变因子 | 置信:70% | 捕捉短期波动率相对于长期波动率的突变，反映市场状态从稳定到混乱的切换。当短期波动率远高于长期波动率时，市场可能进入未知状态导致止损或超时。使用短期波动率与长期波动率的比率，并压缩到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeChangeDetector(BaseFactor):
    """捕捉短期波动率相对于长期波动率的突变，反映市场状态从稳定到混乱的切换。当短期波动率远高于长期波动率时，市场可能进入未知状态导致止损或超时。使用短期波动率与长期波动率的比率，并压缩到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_regime_change",
            name="Volatility Regime Change Detector",
            display_name="波动率状态突变因子",
            description="捕捉短期波动率相对于长期波动率的突变，反映市场状态从稳定到混乱的切换。当短期波动率远高于长期波动率时，市场可能进入未知状态导致止损或超时。使用短期波动率与长期波动率的比率，并压缩到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算收益率
        ret = data['close'].pct_change()
        # 短期波动率（3日）和长期波动率（20日）
        short_vol = ret.rolling(3, min_periods=1).std()
        long_vol = ret.rolling(20, min_periods=1).std().clip(lower=1e-6)
        ratio = short_vol / long_vol
        # 标准化：减去1表示偏离程度
        deviation = ratio - 1.0
        # 使用tanh压缩到[-1,1]
        result = np.tanh(deviation * 3)  # 调整增益
        return result
