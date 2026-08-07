"""AI因子: 市场熵 | 置信:55% | 基于价格分布的香农熵，衡量市场不确定性。将最近L根K线的收盘价离散化到Q个区间，计算概率分布熵。当熵值高时，价格分布均匀，缺乏明确趋势或状态，对应regime=unknown。输出经双曲正切映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Market_Entropy(BaseFactor):
    """基于价格分布的香农熵，衡量市场不确定性。将最近L根K线的收盘价离散化到Q个区间，计算概率分布熵。当熵值高时，价格分布均匀，缺乏明确趋势或状态，对应regime=unknown。输出经双曲正切映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_entropy",
            name="Market_Entropy",
            display_name="市场熵",
            description="基于价格分布的香农熵，衡量市场不确定性。将最近L根K线的收盘价离散化到Q个区间，计算概率分布熵。当熵值高时，价格分布均匀，缺乏明确趋势或状态，对应regime=unknown。输出经双曲正切映射到[-1,1]。",
            category="behavioral",
            subcategory="entropy",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        L = 20
        Q = 10
        close = data['close']
        # 滚动窗口计算熵
        def entropy(series):
            if len(series) < Q:
                return 0.0
            bins = np.linspace(series.min(), series.max(), Q+1)
            digitized = np.digitize(series, bins) - 1
            digitized = np.clip(digitized, 0, Q-1)
            counts = np.bincount(digitized, minlength=Q)
            probs = counts / len(series)
            probs = probs[probs > 0]
            return -np.sum(probs * np.log2(probs))
        entropy_series = close.rolling(L).apply(entropy, raw=False)
        # 归一化：最大熵为log2(Q)，将熵除以log2(Q)得到[0,1]再映射到[-1,1]
        max_entropy = np.log2(Q)
        normalized = entropy_series / max_entropy
        result = 2 * normalized - 1
        return result.fillna(0.0).clip(-1.0, 1.0)
