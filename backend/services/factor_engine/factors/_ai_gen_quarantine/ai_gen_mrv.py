"""AI因子: 均值回复速度 | 置信:60% | 衡量价格偏离均线后的回归速度，当偏离较大但回归速度慢时，易发生持有亏损。通过价格与EMA的差值及其变化率，识别过度偏离后的弱势整理。输出[-1,1]，负值表示弱势偏离且无回归迹象。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Velocity(BaseFactor):
    """衡量价格偏离均线后的回归速度，当偏离较大但回归速度慢时，易发生持有亏损。通过价格与EMA的差值及其变化率，识别过度偏离后的弱势整理。输出[-1,1]，负值表示弱势偏离且无回归迹象。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrv",
            name="Mean_Reversion_Velocity",
            display_name="均值回复速度",
            description="衡量价格偏离均线后的回归速度，当偏离较大但回归速度慢时，易发生持有亏损。通过价格与EMA的差值及其变化率，识别过度偏离后的弱势整理。输出[-1,1]，负值表示弱势偏离且无回归迹象。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 计算5日和20日EMA
        ema5 = close.ewm(span=5, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        # 偏离度
        dev = (close - ema20) / (close + 1e-8)
        # 偏离变化率（回归速度）
        dev_change = dev.diff()
        # 回归动量: 当偏离为正且dev_change为负时表示回归，为负时表示背离
        # 组合: 如果偏离大且回归速度慢，则信号弱
        # 使用sign* (abs(dev) * (1 - abs(dev_change)*20)) 再归一化
        raw = np.sign(dev) * (np.abs(dev) * (1 - np.clip(np.abs(dev_change)*20, 0, 0.99)))
        result = np.tanh(raw * 50)
        return result.fillna(0).clip(-1,1)
