"""AI因子: 波动率自适应动量 | 置信:65% | 根据近期波动率调整动量信号的强度，在低波动环境中降低方向性暴露以规避假突破和止损陷阱。使用过去N天的标准化收益率除以波动率，并用smooth函数压缩到[-1,1]。高波动时动量信号更强，低波动时趋于零。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdaptiveMomentum(BaseFactor):
    """根据近期波动率调整动量信号的强度，在低波动环境中降低方向性暴露以规避假突破和止损陷阱。使用过去N天的标准化收益率除以波动率，并用smooth函数压缩到[-1,1]。高波动时动量信号更强，低波动时趋于零。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adapt_momentum",
            name="Volatility Adaptive Momentum",
            display_name="波动率自适应动量",
            description="根据近期波动率调整动量信号的强度，在低波动环境中降低方向性暴露以规避假突破和止损陷阱。使用过去N天的标准化收益率除以波动率，并用smooth函数压缩到[-1,1]。高波动时动量信号更强，低波动时趋于零。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        returns = close.pct_change()
        # 20日波动率
        vol = returns.rolling(20, min_periods=1).std() * (252**0.5)
        # 动量: 10日累计收益率
        mom = close.pct_change(10)
        # 波动率调整动量
        adapt_mom = mom / (vol + 1e-10)
        # 压缩到[-1,1] 使用tanh
        result = np.tanh(adapt_mom * 5)  # 缩放因子调节灵敏度
        return result.fillna(0.0)
