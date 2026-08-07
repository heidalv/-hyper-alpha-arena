"""AI因子: 动量成交量一致性 | 置信:60% | 结合价格效率比（净变化/总波动）与成交量相对变化，衡量趋势强度与市场参与度的同步性。当效率比绝对值低且成交量萎缩时，表明趋势不明确，输出负信号；反之输出正信号。使用tanh归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Volume_Consistency(BaseFactor):
    """结合价格效率比（净变化/总波动）与成交量相对变化，衡量趋势强度与市场参与度的同步性。当效率比绝对值低且成交量萎缩时，表明趋势不明确，输出负信号；反之输出正信号。使用tanh归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momv",
            name="Momentum Volume Consistency",
            display_name="动量成交量一致性",
            description="结合价格效率比（净变化/总波动）与成交量相对变化，衡量趋势强度与市场参与度的同步性。当效率比绝对值低且成交量萎缩时，表明趋势不明确，输出负信号；反之输出正信号。使用tanh归一化。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        # 效率比：20日净变化 / 20日总波动
        net_change = close - close.shift(20)
        total_volatility = (close.diff().abs()).rolling(20).sum()
        eff_ratio = net_change / total_volatility
        # 成交量相对变化
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        # 原始得分：效率比（绝对值越大趋势越强）乘以成交量比率（>1表示放量）
        raw = eff_ratio * (vol_ratio - 1) * 5
        result = pd.Series(np.tanh(raw), index=data.index)
        return result
