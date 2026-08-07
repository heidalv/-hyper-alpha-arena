"""AI因子: 动量一致性因子 | 置信:60% | 计算短期（5日）、中期（20日）、长期（60日）动量方向（使用ROC），如果三者同为正或同为负，则赋予强信号+1或-1；如果方向不一致则赋予接近0的值。信号越强表示趋势越确定，避免在方向不明时开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Consistency_Factor(BaseFactor):
    """计算短期（5日）、中期（20日）、长期（60日）动量方向（使用ROC），如果三者同为正或同为负，则赋予强信号+1或-1；如果方向不一致则赋予接近0的值。信号越强表示趋势越确定，避免在方向不明时开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momconsistency",
            name="Momentum Consistency Factor",
            display_name="动量一致性因子",
            description="计算短期（5日）、中期（20日）、长期（60日）动量方向（使用ROC），如果三者同为正或同为负，则赋予强信号+1或-1；如果方向不一致则赋予接近0的值。信号越强表示趋势越确定，避免在方向不明时开仓。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 计算不同周期的ROC (rate of change)
        roc_short = close.pct_change(periods=5)
        roc_mid = close.pct_change(periods=20)
        roc_long = close.pct_change(periods=60)
        # 取符号
        sign_short = np.sign(roc_short)
        sign_mid = np.sign(roc_mid)
        sign_long = np.sign(roc_long)
        # 判断是否一致
        sum_sign = sign_short + sign_mid + sign_long
        # 如果三者同向，sum_sign的绝对值为3；否则为1或-1等
        # 映射： sum_sign/3 得到[-1, -0.333, 0.333, 1] 但我们需要连续值，可考虑用乘积的绝对值
        consistency = (sign_short * sign_mid * sign_long)  # 同向时为1或-1，否则为0? 实际有NaN
        # 但乘积累积了NaN，用fillna
        result = consistency.fillna(0)
        # 如果短期和中期一致但长期缺失，可考虑补充，但这里简化
        # 为了平滑，结合三个ROC的均值方向
        avg_roc = (roc_short.fillna(0) + roc_mid.fillna(0) + roc_long.fillna(0)) / 3
        # 当一致性为0时用平均ROC方向占位
        no_consistency = (consistency == 0)
        result[no_consistency] = np.tanh(avg_roc[no_consistency] * 10)  # 缩放
        return result.fillna(0).clip(-1, 1)
