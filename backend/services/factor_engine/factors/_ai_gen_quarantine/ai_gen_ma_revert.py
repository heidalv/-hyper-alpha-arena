"""AI因子: 均线偏离反转 | 置信:65% | 当价格大幅偏离短期均线（如5日均线）且偏离度达到阈值时，预测短期内价格向均线回归。适用于高杠杆追高亏损模式，通过捕捉超买/超卖反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MovingAverageDeviationReversal(BaseFactor):
    """当价格大幅偏离短期均线（如5日均线）且偏离度达到阈值时，预测短期内价格向均线回归。适用于高杠杆追高亏损模式，通过捕捉超买/超卖反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ma_revert",
            name="Moving Average Deviation Reversal",
            display_name="均线偏离反转",
            description="当价格大幅偏离短期均线（如5日均线）且偏离度达到阈值时，预测短期内价格向均线回归。适用于高杠杆追高亏损模式，通过捕捉超买/超卖反转信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        ma5 = close.rolling(5).mean()
        dev = (close - ma5) / ma5
        # 阈值取历史分位动态或固定0.02
        threshold = 0.02
        result = -dev.clip(-threshold, threshold) / threshold  # 反转信号：正偏离=>负值，负偏离=>正值
        return result.clip(-1, 1)
