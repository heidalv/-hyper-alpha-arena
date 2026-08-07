"""AI因子: 动量衰减 | 置信:60% | 比较短期(5日)与长期(20日)动量差值，当短期动量弱于长期时发出做多风险信号，配合亏损模式中的追涨止损现象。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDecay(BaseFactor):
    """比较短期(5日)与长期(20日)动量差值，当短期动量弱于长期时发出做多风险信号，配合亏损模式中的追涨止损现象。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momd",
            name="Momentum Decay",
            display_name="动量衰减",
            description="比较短期(5日)与长期(20日)动量差值，当短期动量弱于长期时发出做多风险信号，配合亏损模式中的追涨止损现象。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ret5 = close.pct_change(5)
        ret20 = close.pct_change(20)
        diff = ret5 - ret20
        # 将差值映射到[-1,1]，正值表示短期动量强于长期（相对安全），负值表示衰减（风险）
        result = np.tanh(diff)
        return result.fillna(0.0)
