"""AI因子: 趋势一致性与成交量衰减 | 置信:60% | 衡量过去N根K线价格方向一致性（连续同向K线比例）与成交量衰减率。当趋势微弱（一致性低）且成交量持续下降时，极易触发止损和持仓超时亏损。因子负值表示高风险混乱状态，正值表示健康趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendCoherenceVolumeDecay(BaseFactor):
    """衡量过去N根K线价格方向一致性（连续同向K线比例）与成交量衰减率。当趋势微弱（一致性低）且成交量持续下降时，极易触发止损和持仓超时亏损。因子负值表示高风险混乱状态，正值表示健康趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_coher",
            name="Trend Coherence & Volume Decay",
            display_name="趋势一致性与成交量衰减",
            description="衡量过去N根K线价格方向一致性（连续同向K线比例）与成交量衰减率。当趋势微弱（一致性低）且成交量持续下降时，极易触发止损和持仓超时亏损。因子负值表示高风险混乱状态，正值表示健康趋势。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 10
        # 价格方向向量：1 if close > open else -1
        direction = np.where(data['close'] > data['open'], 1, -1)
        # 方向一致性：过去n根中正向比例减去负向比例
        pos_count = np.array([np.sum(direction[i-n:i] > 0) for i in range(n, len(direction)+1)])
        neg_count = n - pos_count
        coherence = (pos_count - neg_count) / n
        # 成交量衰减：5期均量 / 20期均量，小于1表示衰减
        vol5 = data['volume'].rolling(5).mean()
        vol20 = data['volume'].rolling(20).mean()
        vol_decay = vol5 / (vol20 + 1e-10)
        # 综合信号：低一致性 + 低成交量 = 混乱市场（负值）
        raw = coherence * (vol_decay - 0.5) * 2
        # 平滑并归一化
        result = raw.rolling(3).mean().fillna(0)
        result = np.clip(result, -1, 1)
        # 对齐长度
        result = pd.Series(result, index=data.index[n-1:])
        result = result.reindex(data.index, method='ffill').fillna(0)
        return result
