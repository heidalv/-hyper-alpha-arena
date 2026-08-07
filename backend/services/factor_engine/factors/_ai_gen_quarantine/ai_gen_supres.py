"""AI因子: 动态支撑阻力偏离因子 | 置信:60% | 计算当前收盘价相对于近期价格轨道的偏离程度。使用过去20周期最高价和最低价构建轨道，结合ATR衡量偏离距离。当价格接近轨道边缘且动量不足时容易触发止损，因子输出负值表示风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Supportresistancedeviation(BaseFactor):
    """计算当前收盘价相对于近期价格轨道的偏离程度。使用过去20周期最高价和最低价构建轨道，结合ATR衡量偏离距离。当价格接近轨道边缘且动量不足时容易触发止损，因子输出负值表示风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_supres",
            name="SupportResistanceDeviation",
            display_name="动态支撑阻力偏离因子",
            description="计算当前收盘价相对于近期价格轨道的偏离程度。使用过去20周期最高价和最低价构建轨道，结合ATR衡量偏离距离。当价格接近轨道边缘且动量不足时容易触发止损，因子输出负值表示风险。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 近期高低点（20周期）
        recent_high = high.rolling(20).max()
        recent_low = low.rolling(20).min()
        # 价格在轨道中的位置 (0~1)
        range_ = recent_high - recent_low
        pos = (close - recent_low) / range_.replace(0, np.nan)
        # 计算ATR(14)用于衡量偏离幅度
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 偏离程度：价格接近0或1时，因子向极端值倾斜，乘以atr倒数调节
        deviation = np.minimum(pos, 1 - pos) * 2  # 0.5->1, 0->0
        # 接近边缘时信号更负（风险），偏离中间时为正
        signal = - (1 - deviation)  # 中间为0，边缘为-1
        # 用ATR缩放：小ATR（窄幅）时信号更敏感
        atr_norm = atr / close.rolling(20).mean().replace(0, np.nan)
        scaled = signal * (1 + atr_norm.fillna(0.01))
        result = scaled.clip(-1, 1)
        return result.fillna(0)
