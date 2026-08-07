"""AI因子: 未知市场状态回避做空 | 置信:55% | 通过波动率异常、成交量离散度和多空强度综合判断市场是否处于‘未知’状态，若处于则发出强做多信号（-1反向），否则中性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeAvoidShort(BaseFactor):
    """通过波动率异常、成交量离散度和多空强度综合判断市场是否处于‘未知’状态，若处于则发出强做多信号（-1反向），否则中性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown",
            name="Unknown Regime Avoid Short",
            display_name="未知市场状态回避做空",
            description="通过波动率异常、成交量离散度和多空强度综合判断市场是否处于‘未知’状态，若处于则发出强做多信号（-1反向），否则中性。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 波动率异常：当前ATR与近期均值之比
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_ratio = atr / atr.rolling(50).mean()
        # 成交量离散度：当前成交量与均值的偏差
        vol_z = (volume - volume.rolling(20).mean()) / volume.rolling(20).std()
        # 多空强度：日内方向 (收盘-开盘) / (最高-最低+eps)
        hl = high - low + 1e-10
        strength = (close - data['open']) / hl
        strength_abs = strength.abs()
        # 综合得分：波动率高、成交量异常、方向性弱
        score = (atr_ratio > 1.5).astype(int) + (vol_z.abs() > 2).astype(int) + (strength_abs < 0.1).astype(int)
        # 得分>=2 视为未知状态，做多信号(负值表示避免做空)
        signal = np.where(score >= 2, -1, 0)
        # 归一化到[-1,1]
        result = pd.Series(signal, index=data.index)
        return result
