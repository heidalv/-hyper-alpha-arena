"""AI因子: 市场状态模糊度 | 置信:60% | 量化当前市场趋势的模糊程度。当价格在短期均线与长期均线之间反复穿越且动量指标（如RSI）在中性区域震荡时，表明市场处于未知/混沌状态，应避免交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeAmbiguity(BaseFactor):
    """量化当前市场趋势的模糊程度。当价格在短期均线与长期均线之间反复穿越且动量指标（如RSI）在中性区域震荡时，表明市场处于未知/混沌状态，应避免交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_ambiguity",
            name="Regime Ambiguity",
            display_name="市场状态模糊度",
            description="量化当前市场趋势的模糊程度。当价格在短期均线与长期均线之间反复穿越且动量指标（如RSI）在中性区域震荡时，表明市场处于未知/混沌状态，应避免交易。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 短期均线（10日）和长期均线（50日）
        ma10 = close.rolling(10).mean()
        ma50 = close.rolling(50).mean()
        # 计算价格在两条均线之间的相对位置
        spread = ma10 - ma50
        # 标准化为Z-score（滚动20日）
        z = (spread - spread.rolling(20).mean()) / spread.rolling(20).std()
        # RSI(14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        # 模糊条件：Z-score绝对值小于0.5 且 RSI在40-60之间
        condition = (np.abs(z) < 0.5) & (rsi >= 40) & (rsi <= 60)
        # 映射：模糊时为-1（建议回避），否则0
        result = np.where(condition, -1.0, 0.0)
        return pd.Series(result, index=data.index)
