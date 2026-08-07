"""AI因子: 波动率调整均值回复 | 置信:70% | 计算当前价格相对于20周期均线的偏离度，并用20周期真实波幅(ATR)进行标准化。当偏离度大但波动率小时，可能发生均值回复，因子值为负; 当偏离度小或波动率大时，因子值接近0或正，用于过滤震荡行情中的假突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Rescaled_Mean_Reversion(BaseFactor):
    """计算当前价格相对于20周期均线的偏离度，并用20周期真实波幅(ATR)进行标准化。当偏离度大但波动率小时，可能发生均值回复，因子值为负; 当偏离度小或波动率大时，因子值接近0或正，用于过滤震荡行情中的假突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vrf",
            name="Volatility_Rescaled_Mean_Reversion",
            display_name="波动率调整均值回复",
            description="计算当前价格相对于20周期均线的偏离度，并用20周期真实波幅(ATR)进行标准化。当偏离度大但波动率小时，可能发生均值回复，因子值为负; 当偏离度小或波动率大时，因子值接近0或正，用于过滤震荡行情中的假突破。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算20周期简单移动平均
        sma20 = data['close'].rolling(20).mean()
        # 偏离度 (百分比)
        deviation = (data['close'] - sma20) / sma20
        # 计算ATR (20周期)
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift(1))
        low_close = np.abs(data['low'] - data['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        # 相对价格标准化ATR
        atr_ratio = atr / sma20
        # 标准化偏离度: 偏离度除以ATR比例 (加小量防除零)
        z_score = deviation / (atr_ratio + 1e-10)
        # 压缩到[-1,1]: 使用tanh或clip
        factor = np.clip(z_score * 2, -1, 1)  # 适度拉伸
        # 当偏离度绝对值很大但ATR比例很小，z_score很大，因子接近±1；否则接近0
        return factor.fillna(0)
