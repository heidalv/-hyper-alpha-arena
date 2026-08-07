"""AI因子: 多周期收敛反转 | 置信:50% | 计算短周期（5）和长周期（20）收益率的相关性，以及价格通道宽度，当多周期方向不一致且通道收窄时，市场进入随机状态，易出现止损。利用均值回复特性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeConvergence(BaseFactor):
    """计算短周期（5）和长周期（20）收益率的相关性，以及价格通道宽度，当多周期方向不一致且通道收窄时，市场进入随机状态，易出现止损。利用均值回复特性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_multifast",
            name="Multi_Timeframe_Convergence",
            display_name="多周期收敛反转",
            description="计算短周期（5）和长周期（20）收益率的相关性，以及价格通道宽度，当多周期方向不一致且通道收窄时，市场进入随机状态，易出现止损。利用均值回复特性。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 多周期收益率
        ret5 = close.pct_change(5)
        ret20 = close.pct_change(20)
        # 方向一致性：如果两者同号则一致性高，否则低
        # 使用乘积的符号，负值表示分歧
        direction_div = np.sign(ret5) * np.sign(ret20)
        # 通道宽度（衡量波动收敛）
        high20 = high.rolling(20).max()
        low20 = low.rolling(20).min()
        channel_width = (high20 - low20) / (close + 1e-8)
        width_rank = channel_width.rolling(50).rank(pct=True)  # 相对宽度
        # 当方向分歧且通道宽度处于历史低位时，反转概率高
        # 信号：分歧+窄通道 => 预期反转收益为正（均值回复）
        # 使用过去短期收益率的方向来指示信号极性
        signal = np.where((direction_div == -1) & (width_rank < 0.3), -np.sign(ret5 + ret20), 0.0)
        # 平滑
        result = signal.rolling(3).mean().fillna(0.0).clip(-1,1)
        return result
