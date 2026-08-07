"""AI因子: 趋势不稳定因子 | 置信:55% | 使用短期内价格方向一致性度量趋势稳定性，当趋势频繁反转时表明市场处于无方向状态（regime unknown）。计算归一化后的方向变化率。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Instability_Factor(BaseFactor):
    """使用短期内价格方向一致性度量趋势稳定性，当趋势频繁反转时表明市场处于无方向状态（regime unknown）。计算归一化后的方向变化率。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_instability",
            name="Trend Instability Factor",
            display_name="趋势不稳定因子",
            description="使用短期内价格方向一致性度量趋势稳定性，当趋势频繁反转时表明市场处于无方向状态（regime unknown）。计算归一化后的方向变化率。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        ret = close.pct_change().fillna(0)
        # 计算过去N根K线的方向符号序列
        N = 10
        signs = np.sign(ret)
        # 计算符号变化次数
        changes = signs.diff().ne(0).astype(int).rolling(N).sum()
        # 归一化到[-1,1]，变化越多越不稳定（负值？设定0为中立，正表示不稳定？可定义因子值正为不稳定，负为稳定）
        # 使用max变化为N-1，将变化次数映射到[-1,1]
        max_changes = N - 1
        result = 2 * (changes / max_changes) - 1
        return result.fillna(0)
