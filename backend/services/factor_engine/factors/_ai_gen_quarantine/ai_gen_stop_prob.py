"""AI因子: 止损触发风险 | 置信:60% | 近期价格冲击强度与波动率之比，衡量当前价格是否处于容易触发止损的高风险区域。负值表示高风险（价格快速反向运动），正值表示低风险。结合ATR与短期回撤计算。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplossriskscore(BaseFactor):
    """近期价格冲击强度与波动率之比，衡量当前价格是否处于容易触发止损的高风险区域。负值表示高风险（价格快速反向运动），正值表示低风险。结合ATR与短期回撤计算。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stop_prob",
            name="StopLossRiskScore",
            display_name="止损触发风险",
            description="近期价格冲击强度与波动率之比，衡量当前价格是否处于容易触发止损的高风险区域。负值表示高风险（价格快速反向运动），正值表示低风险。结合ATR与短期回撤计算。",
            category="technical",
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
        # 计算ATR(14)
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr14 = tr.rolling(14).mean().replace(0, np.nan)
        # 短期最大回撤 (2个周期内)
        recent_high = high.rolling(2).max()
        recent_low = low.rolling(2).min()
        drawdown = (close - recent_low) / (recent_high - recent_low + 1e-10) * 2 - 1  # 映射到[-1,1]，0为中心
        # 用ATR调整：当ATR较大时，波动大，风险高，反转信号
        atr_ratio = atr14 / close.replace(0, np.nan)
        risk = -np.sign(drawdown) * np.clip(atr_ratio * 10, 0, 1)  # 负值表示高风险
        result = (drawdown + risk) / 2  # 组合，约[-1,1]
        result = np.clip(result, -1, 1)
        return result
