"""AI因子: 动量衰减 | 置信:68% | 衡量价格动量的二阶变化（加速度），当动量增速转为负值，预示趋势可能停滞或反转，此时持仓易触发超时亏损。因子负值表示动量衰减中。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDecay(BaseFactor):
    """衡量价格动量的二阶变化（加速度），当动量增速转为负值，预示趋势可能停滞或反转，此时持仓易触发超时亏损。因子负值表示动量衰减中。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_md",
            name="Momentum Decay",
            display_name="动量衰减",
            description="衡量价格动量的二阶变化（加速度），当动量增速转为负值，预示趋势可能停滞或反转，此时持仓易触发超时亏损。因子负值表示动量衰减中。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].astype(float)
        # 计算10日动量
        roc = close.pct_change(10)
        # 动量的5日变化（加速度）
        roc_prev = roc.shift(5)
        momentum_accel = roc - roc_prev
        # 标准化：除以波动率
        vol = close.pct_change().rolling(20).std()
        norm_accel = momentum_accel / (vol + 1e-9)
        # 使用tanh映射到[-1,1]
        result = np.tanh(norm_accel * 5)  # 系数控制敏感度
        return pd.Series(result, index=data.index).fillna(0)
