"""AI因子: 反转失败概率因子 | 置信:60% | 结合短期动量反转指标与波动率扩张，预测当前是否为高风险反转失败场景。先计算短期RSI（14）超买超卖区域，再计算ATR（14）的突变率。当RSI处于极端（<30或>70）且ATR突然放大超过1.5倍均值时，表明市场可能处于趋势加速状态，此时逆势操作极易失败。因子输出正值表示反转失败风险高，应避免反向交易；负值表示顺势信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalFailureProbability(BaseFactor):
    """结合短期动量反转指标与波动率扩张，预测当前是否为高风险反转失败场景。先计算短期RSI（14）超买超卖区域，再计算ATR（14）的突变率。当RSI处于极端（<30或>70）且ATR突然放大超过1.5倍均值时，表明市场可能处于趋势加速状态，此时逆势操作极易失败。因子输出正值表示反转失败风险高，应避免反向交易；负值表示顺势信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_fail",
            name="Reversal Failure Probability",
            display_name="反转失败概率因子",
            description="结合短期动量反转指标与波动率扩张，预测当前是否为高风险反转失败场景。先计算短期RSI（14）超买超卖区域，再计算ATR（14）的突变率。当RSI处于极端（<30或>70）且ATR突然放大超过1.5倍均值时，表明市场可能处于趋势加速状态，此时逆势操作极易失败。因子输出正值表示反转失败风险高，应避免反向交易；负值表示顺势信号。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # RSI计算
        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # ATR计算
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift(1))
        low_close = np.abs(data['low'] - data['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_ratio = atr / (atr.rolling(30).mean() + 1e-10)
        # 极端RSI区域：<30或>70
        extreme = (rsi < 30) | (rsi > 70)
        # ATR突然扩张：>1.5倍
        vol_spike = (atr_ratio > 1.5)
        # 组合信号：极端+放量 => 风险高
        risk = extreme & vol_spike
        # 方向：若RSI小于30为超卖（应该做多），但放量可能继续下跌；反之亦然
        direction = np.where(rsi < 30, 1, -1)  # 超卖做多方向为+1，超买做空方向为-1
        # 最终因子：若risk为True，则输出方向值的相反（即失败风险），否则0
        result = np.where(risk, -direction, 0.0)
        # 归一化到[-1,1]
        result = np.clip(result, -1, 1)
        return pd.Series(result, index=data.index)
