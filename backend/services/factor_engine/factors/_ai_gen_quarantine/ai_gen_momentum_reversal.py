"""AI因子: 动量反转因子 | 置信:60% | 基于短期与长期动量差异预测反转。当短期动量（5日）远高于长期动量（20日）时，发出看空信号；反之看多。使用两者的差值并归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Reversal(BaseFactor):
    """基于短期与长期动量差异预测反转。当短期动量（5日）远高于长期动量（20日）时，发出看空信号；反之看多。使用两者的差值并归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_reversal",
            name="Momentum_Reversal",
            display_name="动量反转因子",
            description="基于短期与长期动量差异预测反转。当短期动量（5日）远高于长期动量（20日）时，发出看空信号；反之看多。使用两者的差值并归一化。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算短期和长期动量
        short_mom = data['close'] / data['close'].shift(5) - 1
        long_mom = data['close'] / data['close'].shift(20) - 1
        # 差值，正值表示短期强于长期
        diff = short_mom - long_mom
        # 用滚动均值与标准差归一化
        mean = diff.rolling(50, min_periods=1).mean()
        std = diff.rolling(50, min_periods=1).std()
        z = (diff - mean) / std
        result = np.clip(-z / 2, -1, 1)  # 取负号，因为反转：短期强则看空
        return result.fillna(0)
