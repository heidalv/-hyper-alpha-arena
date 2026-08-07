"""AI因子: 布林带张力因子 | 置信:55% | 基于布林带宽度变化，当带宽压缩至历史低位时，市场处于方向不明的挤压状态，此时做多风险高，因子输出负值；带宽扩张且价格位于中轨以上则输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class bollinger_band_tension(BaseFactor):
    """基于布林带宽度变化，当带宽压缩至历史低位时，市场处于方向不明的挤压状态，此时做多风险高，因子输出负值；带宽扩张且价格位于中轨以上则输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_tension",
            name="bollinger_band_tension",
            display_name="布林带张力因子",
            description="基于布林带宽度变化，当带宽压缩至历史低位时，市场处于方向不明的挤压状态，此时做多风险高，因子输出负值；带宽扩张且价格位于中轨以上则输出正值。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 20日布林带
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        bandwidth = 2 * std / sma  # 相对带宽
        # 计算带宽的20日分位数
        bw_rank = bandwidth.rolling(20).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=True)
        # 价格在中轨上的位置
        price_position = (close - sma) / (2 * std + 1e-10)  # 标准化
        # 当带宽极窄且价格靠近中轨时 => 未知状态，负值
        tension = bandwidth.rolling(20).mean() / bandwidth  # 越大表示带宽低于均值
        tension_signal = np.clip(1 - bw_rank * 2, -1, 1)  # 带宽越小越负
        # 结合价格位置：价格偏离中轨过多时也不安全，但这里主要用带宽
        factor = tension_signal * 0.7 + np.clip(price_position, -1, 1) * 0.3
        factor = factor.clip(-1, 1)
        return factor
