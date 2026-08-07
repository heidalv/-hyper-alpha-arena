"""AI因子: 动量反转强度指数 | 置信:60% | 结合相对强弱指标（RSI）和动量衰减，捕捉因流动性磁铁或AI反指导致的突然反转。在RSI超买/超卖区域且动量快速衰减时，反转概率高。因子正为看跌反转信号，负为看涨反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumReversalStrengthIndex(BaseFactor):
    """结合相对强弱指标（RSI）和动量衰减，捕捉因流动性磁铁或AI反指导致的突然反转。在RSI超买/超卖区域且动量快速衰减时，反转概率高。因子正为看跌反转信号，负为看涨反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrsi",
            name="Momentum_Reversal_Strength_Index",
            display_name="动量反转强度指数",
            description="结合相对强弱指标（RSI）和动量衰减，捕捉因流动性磁铁或AI反指导致的突然反转。在RSI超买/超卖区域且动量快速衰减时，反转概率高。因子正为看跌反转信号，负为看涨反转。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # 动量衰减：过去3周期价格变化的变化率
        mom = close.diff(3)
        mom_decay = mom.diff()  # 二阶差分
        # 归一化
        rsi_norm = (rsi - 50) / 50  # [-1,1]
        # 当RSI极端且动量衰减为负（向上动量减弱）时，看跌反转信号
        signal = -rsi_norm * (mom_decay / (mom.rolling(10).std() + 1e-10)).clip(-1, 1)
        return signal.clip(-1, 1)
