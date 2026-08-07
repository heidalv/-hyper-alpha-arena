"""AI因子: 收益率熵 | 置信:60% | 计算过去20天收益率分布的香农熵，衡量市场随机性。高熵意味着收益率分布均匀，市场无明显趋势，属于未知混乱状态，容易导致各种错误。熵值归一化到[0,1]后，映射到[-1,1]：高熵（混乱）对应负值，低熵（趋势明确）对应正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Return_Entropy(BaseFactor):
    """计算过去20天收益率分布的香农熵，衡量市场随机性。高熵意味着收益率分布均匀，市场无明显趋势，属于未知混乱状态，容易导致各种错误。熵值归一化到[0,1]后，映射到[-1,1]：高熵（混乱）对应负值，低熵（趋势明确）对应正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ent",
            name="Return Entropy",
            display_name="收益率熵",
            description="计算过去20天收益率分布的香农熵，衡量市场随机性。高熵意味着收益率分布均匀，市场无明显趋势，属于未知混乱状态，容易导致各种错误。熵值归一化到[0,1]后，映射到[-1,1]：高熵（混乱）对应负值，低熵（趋势明确）对应正值。",
            category="behavioral",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        ret = close.pct_change().fillna(0)
        window = 20
        def entropy(series):
            if len(series) < 2:
                return 0.5
            hist, _ = np.histogram(series, bins=10, range=(-0.1, 0.1), density=True)
            hist = hist[hist > 0]
            if len(hist) == 0:
                return 0.5
            ent = -np.sum(hist * np.log2(hist + 1e-10))
            max_ent = np.log2(10)
            return ent / max_ent
        norm_ent = ret.rolling(window).apply(entropy, raw=False)
        factor = -2 * norm_ent + 1  # [0,1] -> [-1,1] 高熵负值
        factor = factor.fillna(0)
        return factor
