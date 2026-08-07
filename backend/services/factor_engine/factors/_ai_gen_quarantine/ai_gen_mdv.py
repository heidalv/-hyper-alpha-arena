"""AI因子: 动量背离 | 置信:60% | 短期动量与长期动量反向时产生背离信号。计算短期（5日）收益率和长期（20日）收益率，若短期为正但长期为负，表明反弹可能是陷阱，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Divergence(BaseFactor):
    """短期动量与长期动量反向时产生背离信号。计算短期（5日）收益率和长期（20日）收益率，若短期为正但长期为负，表明反弹可能是陷阱，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mdv",
            name="Momentum Divergence",
            display_name="动量背离",
            description="短期动量与长期动量反向时产生背离信号。计算短期（5日）收益率和长期（20日）收益率，若短期为正但长期为负，表明反弹可能是陷阱，输出负值。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        short_ret = data['close'].pct_change(5).fillna(0)
        long_ret = data['close'].pct_change(20).fillna(0)
        # 背离：短期涨但长期跌
        divergence = ((short_ret > 0) & (long_ret < 0)).astype(float)
        # 强度用短期动量大小
        strength = np.abs(short_ret)
        factor = -divergence * np.clip(strength * 20, 0, 1)
        factor = np.clip(factor, -1, 1)
        return factor
