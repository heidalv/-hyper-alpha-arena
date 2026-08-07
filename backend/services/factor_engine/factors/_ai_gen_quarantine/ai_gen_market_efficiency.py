"""AI因子: 市场效率指标 | 置信:60% | 基于价格序列的随机性度量。计算滚动窗口内价格方向变化次数与总变化次数的比值，越接近0.5表明市场越随机（高效），越偏离则趋势明显。通过线性变换映射到[-1,1]，正值表示趋势明确，负值表示混乱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketEfficiencyRatio(BaseFactor):
    """基于价格序列的随机性度量。计算滚动窗口内价格方向变化次数与总变化次数的比值，越接近0.5表明市场越随机（高效），越偏离则趋势明显。通过线性变换映射到[-1,1]，正值表示趋势明确，负值表示混乱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_market_efficiency",
            name="Market Efficiency Ratio",
            display_name="市场效率指标",
            description="基于价格序列的随机性度量。计算滚动窗口内价格方向变化次数与总变化次数的比值，越接近0.5表明市场越随机（高效），越偏离则趋势明显。通过线性变换映射到[-1,1]，正值表示趋势明确，负值表示混乱。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        window = 20
        # 计算价格方向
        diff = close.diff()
        sign = np.sign(diff)
        # 统计窗口内正负比例
        pos_count = (sign > 0).rolling(window).sum()
        neg_count = (sign < 0).rolling(window).sum()
        total = pos_count + neg_count
        # 比例，避免除零
        ratio = (pos_count - neg_count) / total.replace(0, np.nan)
        # ratio 在[-1,1]之间，但0附近表示随机
        # 我们希望正值表示趋势明确，负值表示混乱，直接使用ratio
        return ratio.fillna(0)
