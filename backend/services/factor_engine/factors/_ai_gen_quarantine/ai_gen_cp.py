"""AI因子: 价格紧凑率 | 置信:50% | 近期K线实体占比与影线长度的比值。紧凑率低说明实体小、影线长，市场犹豫不决，趋势信号可靠性低。因子值正表示实体长、方向明确，负表示影线长、方向不明。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Compactness Ratio(BaseFactor):
    """近期K线实体占比与影线长度的比值。紧凑率低说明实体小、影线长，市场犹豫不决，趋势信号可靠性低。因子值正表示实体长、方向明确，负表示影线长、方向不明。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_cp",
            name="Compactness Ratio",
            display_name="价格紧凑率",
            description="近期K线实体占比与影线长度的比值。紧凑率低说明实体小、影线长，市场犹豫不决，趋势信号可靠性低。因子值正表示实体长、方向明确，负表示影线长、方向不明。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data: pd.DataFrame) -> pd.Series:
            import numpy as np
            # 计算过去5根K线的平均实体占比
            n = 5
            body = abs(data['close'] - data['open'])
            upper_wick = data['high'] - np.maximum(data['close'], data['open'])
            lower_wick = np.minimum(data['close'], data['open']) - data['low']
            total_range = data['high'] - data['low']
            # 避免除零
            total_range = np.where(total_range == 0, 1e-6, total_range)
            body_ratio = body / total_range
            wick_ratio = (upper_wick + lower_wick) / total_range
            # 紧凑率 = body_ratio / (wick_ratio + 1e-6) 然后标准化
            compact = body_ratio / (wick_ratio + 1e-6)
            # 取过去n期均值，用log压缩
            compact_avg = compact.rolling(n).mean()
            # 用log10映射，控制范围
            log_compact = np.log10(compact_avg + 1e-6)
            # 正常范围大致[-1, 1] 通过tanh调整
            result = np.tanh((log_compact + 0.5) * 2)  # 中位数约-0.5附近
            return result.fillna(0)
