"""AI因子: 动量反转因子 | 置信:55% | 捕捉短期动量衰竭和反转风险。计算近期收益与波动率比值，当短期动量快速衰减时预示反转，信号为负（做空）。使用当前bar前5分钟收益减去前20分钟收益，再除以ATR归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class momentum_reversal(BaseFactor):
    """捕捉短期动量衰竭和反转风险。计算近期收益与波动率比值，当短期动量快速衰减时预示反转，信号为负（做空）。使用当前bar前5分钟收益减去前20分钟收益，再除以ATR归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momrv",
            name="momentum_reversal",
            display_name="动量反转因子",
            description="捕捉短期动量衰竭和反转风险。计算近期收益与波动率比值，当短期动量快速衰减时预示反转，信号为负（做空）。使用当前bar前5分钟收益减去前20分钟收益，再除以ATR归一化。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 计算回头率
        ret5 = close.pct_change(5)  # 5周期收益率
        ret20 = close.pct_change(20)  # 20周期收益率
        # 动量差：短期相对长期衰减
        mom_diff = ret5 - ret20
        # ATR
        high = data['high']
        low = data['low']
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        atr_ratio = atr / (close + 1e-8)
        # 归一化：动量差除以波动率
        raw = mom_diff / (atr_ratio + 1e-8)
        # 用tanh压缩到[-1,1]，注意raw>0表示短期动量强于长期，可能延续？但我们寻找反转：当短期疲软时做空
        # 我们希望当差值负且绝对值大时信号为负（做空），正时做多
        result = np.tanh(raw * 2)
        return result.fillna(0)
