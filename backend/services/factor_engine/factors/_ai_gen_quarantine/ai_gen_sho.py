"""AI因子: 影线反转强度 | 置信:50% | 关注K线上下影线长度相对于实体的比例。当上影线过长或下影线过长时，表示价格冲击后迅速回落，容易导致止损被打后反转。因子值正（长上影）或负（长下影）的绝对值大表示高风险。通过符号区分方向，值域[-1,1]：+1表示极端上影空头风险，-1表示极端下影多头风险，0表示无影线。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShadowReversal(BaseFactor):
    """关注K线上下影线长度相对于实体的比例。当上影线过长或下影线过长时，表示价格冲击后迅速回落，容易导致止损被打后反转。因子值正（长上影）或负（长下影）的绝对值大表示高风险。通过符号区分方向，值域[-1,1]：+1表示极端上影空头风险，-1表示极端下影多头风险，0表示无影线。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sho",
            name="Shadow Reversal",
            display_name="影线反转强度",
            description="关注K线上下影线长度相对于实体的比例。当上影线过长或下影线过长时，表示价格冲击后迅速回落，容易导致止损被打后反转。因子值正（长上影）或负（长下影）的绝对值大表示高风险。通过符号区分方向，值域[-1,1]：+1表示极端上影空头风险，-1表示极端下影多头风险，0表示无影线。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        open = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        # 上下影线长度
        upper_shadow = high - np.maximum(open, close)
        lower_shadow = np.minimum(open, close) - low
        body = np.abs(close - open)
        # 避免除以0，加小量
        eps = 1e-10
        # 计算上下影线比例，符号代表方向
        shadow_ratio = (upper_shadow - lower_shadow) / (body + upper_shadow + lower_shadow + eps)
        # 归一化到[-1,1]，由于比例本身在[-1,1]之间，但可能极端，使用tanh增强
        normalized = np.tanh(shadow_ratio * 3)
        return normalized.fillna(0)
