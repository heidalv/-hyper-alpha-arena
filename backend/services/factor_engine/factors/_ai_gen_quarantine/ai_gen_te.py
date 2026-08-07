"""AI因子: 趋势衰竭振荡器 | 置信:65% | 基于价格偏离均线的加速度和波动率标准化，识别趋势可能衰竭的反转点。正值为趋势加速，负值暗示衰竭或反转风险上升，适合在持仓时间过长或移动止损触发前提前减仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustionOscillator(BaseFactor):
    """基于价格偏离均线的加速度和波动率标准化，识别趋势可能衰竭的反转点。正值为趋势加速，负值暗示衰竭或反转风险上升，适合在持仓时间过长或移动止损触发前提前减仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_te",
            name="Trend Exhaustion Oscillator",
            display_name="趋势衰竭振荡器",
            description="基于价格偏离均线的加速度和波动率标准化，识别趋势可能衰竭的反转点。正值为趋势加速，负值暗示衰竭或反转风险上升，适合在持仓时间过长或移动止损触发前提前减仓。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 20周期指数加权均线
        ema = close.ewm(span=20, adjust=False).mean()
        # 偏离与滚动标准差
        deviation = close - ema
        std = deviation.rolling(window=20).std()
        # 标准化偏离
        zscore = deviation / (std + 1e-9)
        # 加速度：zscore的3周期变化率
        accel = zscore.diff(3)
        # 归一化到[-1,1]，使用双曲正切
        result = np.tanh(accel / (accel.rolling(20).std() + 1e-9))
        return result.clip(-1, 1)
