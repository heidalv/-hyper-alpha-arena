"""AI因子: 波动陷阱信号 | 置信:60% | 计算近期(5日)收益率标准差与长期(20日)收益率标准差的比值，比值过高表示短期波动急剧放大，容易触发止损，因子反映该风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityTrapSignal(BaseFactor):
    """计算近期(5日)收益率标准差与长期(20日)收益率标准差的比值，比值过高表示短期波动急剧放大，容易触发止损，因子反映该风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vts",
            name="Volatility Trap Signal",
            display_name="波动陷阱信号",
            description="计算近期(5日)收益率标准差与长期(20日)收益率标准差的比值，比值过高表示短期波动急剧放大，容易触发止损，因子反映该风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ret = close.pct_change()
        std5 = ret.rolling(window=5).std()
        std20 = ret.rolling(window=20).std()
        # 防止除零
        ratio = std5 / (std20 + 1e-10)
        # 当ratio > 1时波动放大，取负并压缩到[-1,1]
        result = -np.tanh(ratio - 1.0)
        return result.fillna(0.0)
