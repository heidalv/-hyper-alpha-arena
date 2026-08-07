"""AI因子: 反转冲突因子 | 置信:60% | 结合短期RSI与长期趋势线的背离，当短期出现反转信号但长期趋势未确认时，市场处于不确定状态，类似master_running_close或ai_reverse失败的情况。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalConflict(BaseFactor):
    """结合短期RSI与长期趋势线的背离，当短期出现反转信号但长期趋势未确认时，市场处于不确定状态，类似master_running_close或ai_reverse失败的情况。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_conflict",
            name="Reversal Conflict",
            display_name="反转冲突因子",
            description="结合短期RSI与长期趋势线的背离，当短期出现反转信号但长期趋势未确认时，市场处于不确定状态，类似master_running_close或ai_reverse失败的情况。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 计算RSI(14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 计算50周期EMA作为长期趋势
        ema50 = close.ewm(span=50, adjust=False).mean()
        # 计算价格相对于ema50的偏离
        price_dev = (close - ema50) / ema50
        # 计算RSI的极端信号：低于30超卖，高于70超买
        rsi_oversold = (rsi < 30).astype(float)
        rsi_overbought = (rsi > 70).astype(float)
        # 判断冲突：超卖但价格仍在EMA下方（下跌趋势未确认），超买但价格在EMA上方（上涨趋势未确认）
        # 这种情况下反转信号与趋势冲突，导致regime unknown
        # 定义冲突信号：超卖且价格低于ema（负向）或超买且价格高于ema（正向）？
        # 我们希望在冲突时避免交易，所以因子应为负向（风险）
        conflict_down = rsi_oversold * (price_dev < -0.02).astype(float)  # 超卖但价格低于均线，下跌趋势未反转
        conflict_up = rsi_overbought * (price_dev > 0.02).astype(float)  # 超买但价格高于均线，上涨趋势未反转
        # 因子：负值表示冲突风险
        factor = -(conflict_down + conflict_up)
        # 平滑并归一化
        result = factor.rolling(3).max()  # 取最大值保持信号持续
        result = np.clip(result * 2 - 1, -1, 1)  # 映射到[-1,1]? 实际已经是-1到0，扩展
        # 改为直接二值化
        result = np.where(result < 0, -1.0, 0.0)
        return pd.Series(result, index=data.index)
