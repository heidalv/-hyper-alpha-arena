"""AI因子: 多周期动量一致性 | 置信:65% | 计算短周期（5日）和长周期（20日）的动量方向，当两者同向时输出正值（强度为动量大小），反向时输出负值（表示方向混乱）。旨在识别市场处于一致趋势还是无序震荡，从而规避regime=unknown。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumCoherence(BaseFactor):
    """计算短周期（5日）和长周期（20日）的动量方向，当两者同向时输出正值（强度为动量大小），反向时输出负值（表示方向混乱）。旨在识别市场处于一致趋势还是无序震荡，从而规避regime=unknown。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_multi_momentum",
            name="Multi-Timeframe Momentum Coherence",
            display_name="多周期动量一致性",
            description="计算短周期（5日）和长周期（20日）的动量方向，当两者同向时输出正值（强度为动量大小），反向时输出负值（表示方向混乱）。旨在识别市场处于一致趋势还是无序震荡，从而规避regime=unknown。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 计算短期动量（5日变化率）和长期动量（20日变化率）
        short_roc = close.pct_change(5)
        long_roc = close.pct_change(20)
        # 动量方向：1表示正，-1表示负
        short_dir = np.sign(short_roc)
        long_dir = np.sign(long_roc)
        # 一致性：同向时取两者绝对值平均，否则取负的平均绝对值
        avg_mag = (short_roc.abs() + long_roc.abs()) / 2.0
        # 归一化到[-1,1]：先clip到0.2（假设最大20%变化），然后缩放
        mag_norm = avg_mag / 0.2
        mag_norm = mag_norm.clip(0, 1)
        # 方向一致时为正值，否则为负值
        coherence = np.where(short_dir == long_dir, 1.0, -1.0)
        result = coherence * mag_norm
        # 填补NaN
        result = result.fillna(0.0)
        result = pd.Series(np.clip(result, -1, 1), index=data.index)
        return result
