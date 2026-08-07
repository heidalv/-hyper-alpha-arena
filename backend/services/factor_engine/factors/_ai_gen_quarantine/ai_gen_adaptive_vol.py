"""AI因子: 自适应波动率状态 | 置信:60% | 根据历史波动率的分位数动态调整风险偏好。当波动率处于极端高位或极端低位时，市场行为往往不规律（unknown regime），此时输出负信号；波动率处于中等水平时输出正信号。同时结合近期波动率变化率增强可靠性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Adaptive_Volatility_Regime(BaseFactor):
    """根据历史波动率的分位数动态调整风险偏好。当波动率处于极端高位或极端低位时，市场行为往往不规律（unknown regime），此时输出负信号；波动率处于中等水平时输出正信号。同时结合近期波动率变化率增强可靠性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adaptive_vol",
            name="Adaptive Volatility Regime",
            display_name="自适应波动率状态",
            description="根据历史波动率的分位数动态调整风险偏好。当波动率处于极端高位或极端低位时，市场行为往往不规律（unknown regime），此时输出负信号；波动率处于中等水平时输出正信号。同时结合近期波动率变化率增强可靠性。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        returns = data['close'].pct_change().fillna(0)
        # 20日滚动波动率
        vol = returns.rolling(20).std()
        # 计算60日历史分位数
        vol_rank = vol.rolling(60).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=False)
        vol_rank = vol_rank.fillna(0.5)
        # 信号：中间区域（0.3~0.7）为正，两端为负
        signal = np.where(vol_rank < 0.2, -1.0, np.where(vol_rank > 0.8, -1.0, 1.0))
        # 加入近期波动率变化：如果最近5日波动率急剧上升或下降，额外惩罚
        vol_change = vol.pct_change(5).fillna(0)
        vol_change_signal = -np.clip(np.abs(vol_change) * 2, 0, 0.5)  # 最大惩罚-0.5
        result = pd.Series(signal, index=data.index) + vol_change_signal
        result = result.clip(-1, 1)
        return result
