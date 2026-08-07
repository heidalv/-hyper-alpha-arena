"""AI因子: 价格效率离散度 | 置信:60% | 测量短期价格方向的稳定性。计算最近M根K线的价格效率比（收盘价变化/总路径长度）的滚动标准差。当效率比波动剧烈时，价格方向不明朗，对应regime=unknown。输出用逆正态化映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Efficiency_Dispersion(BaseFactor):
    """测量短期价格方向的稳定性。计算最近M根K线的价格效率比（收盘价变化/总路径长度）的滚动标准差。当效率比波动剧烈时，价格方向不明朗，对应regime=unknown。输出用逆正态化映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_eff_disp",
            name="Efficiency_Dispersion",
            display_name="价格效率离散度",
            description="测量短期价格方向的稳定性。计算最近M根K线的价格效率比（收盘价变化/总路径长度）的滚动标准差。当效率比波动剧烈时，价格方向不明朗，对应regime=unknown。输出用逆正态化映射到[-1,1]。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        M = 10
        N = 20
        # 计算每根K线的效率比（单根内部）
        high = data['high']
        low = data['low']
        close = data['close']
        # 单根K线总路径：|high-low| + 方向调整（此处简化用high-low）
        path = high - low
        net_change = close.diff().abs()
        # 避免除以0
        eff = net_change / (path + 1e-10)
        # 滚动标准差
        eff_std = eff.rolling(M).std()
        # 再滚动N期归一化
        rank = eff_std.rolling(N).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
        result = 2 * rank - 1  # [0,1] -> [-1,1]
        return result.fillna(0.0).clip(-1.0, 1.0)
