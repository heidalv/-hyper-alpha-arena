"""AI因子: AI反向保护因子 | 置信:60% | 结合短周期动量与相对强弱指标（RSI）的极端值，识别可能触发AI反向信号的区域。当价格在快速下跌后RSI进入超卖区（<30）且出现下影线时，空头易被反向拉升。因子值越高，保护信号越强。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Aireverseguard(BaseFactor):
    """结合短周期动量与相对强弱指标（RSI）的极端值，识别可能触发AI反向信号的区域。当价格在快速下跌后RSI进入超卖区（<30）且出现下影线时，空头易被反向拉升。因子值越高，保护信号越强。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ai_reverse_guard",
            name="AIReverseGuard",
            display_name="AI反向保护因子",
            description="结合短周期动量与相对强弱指标（RSI）的极端值，识别可能触发AI反向信号的区域。当价格在快速下跌后RSI进入超卖区（<30）且出现下影线时，空头易被反向拉升。因子值越高，保护信号越强。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算14周期RSI
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 计算下影线比例： (close - low) / (high - low)
        lower_shadow = (data['close'] - data['low']) / (data['high'] - data['low'] + 1e-10)
        # 短期动量（3日收益率）
        mom3 = data['close'].pct_change(3)
        # 识别条件：快速下跌(mom3<-0.03)，RSI超卖(<30)，下影线长(lower_shadow>0.6)
        condition = (mom3 < -0.03) & (rsi < 30) & (lower_shadow > 0.6)
        # 信号强度：结合下跌幅度和RSI极值
        raw = -mom3 * (30 - rsi) / 30 * lower_shadow
        raw = raw.clip(-1, 1)
        result = raw.where(condition, 0.0)
        return result.fillna(0.0)
