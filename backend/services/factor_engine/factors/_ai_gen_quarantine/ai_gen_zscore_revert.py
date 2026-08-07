"""AI因子: 价格偏离均值风险 | 置信:55% | 计算收盘价相对于20周期均线的Z-score，当偏离过大时（>2或<-2）表示极端状态，但做多时高正偏离容易回归导致亏损。取负值表示做多风险：偏离越大越负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Z_score_Mean_Reversion_Risk(BaseFactor):
    """计算收盘价相对于20周期均线的Z-score，当偏离过大时（>2或<-2）表示极端状态，但做多时高正偏离容易回归导致亏损。取负值表示做多风险：偏离越大越负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_zscore_revert",
            name="Z-score Mean Reversion Risk",
            display_name="价格偏离均值风险",
            description="计算收盘价相对于20周期均线的Z-score，当偏离过大时（>2或<-2）表示极端状态，但做多时高正偏离容易回归导致亏损。取负值表示做多风险：偏离越大越负。",
            category="mean_reversion",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        window = 20
        ma = close.rolling(window).mean()
        std = close.rolling(window).std()
        zscore = (close - ma) / std
        # 映射到[-1,1]，用tanh压缩，并取负值使得高正偏离时因子为负（暗示做多风险）
        result = -np.tanh(zscore / 2)  # 除以2使±2附近饱和
        return result.fillna(0.0)
