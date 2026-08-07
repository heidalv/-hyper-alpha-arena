"""AI因子: 短期反转强度 | 置信:60% | 基于短期均线与长期均线的乖离率以及价格相对布林带上轨的位置，识别极端超买超卖后的反转概率。当乖离率过大且触及布林带上轨时，短期反转向下概率高；乖离率过小且触及下轨时反转向上概率高。因子值正向表示预期向上反转，负向表示向下反转。此因子针对'ai_reverse'和'liq_magnet_reversal'错误模式设计。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTermReversalStrength(BaseFactor):
    """基于短期均线与长期均线的乖离率以及价格相对布林带上轨的位置，识别极端超买超卖后的反转概率。当乖离率过大且触及布林带上轨时，短期反转向下概率高；乖离率过小且触及下轨时反转向上概率高。因子值正向表示预期向上反转，负向表示向下反转。此因子针对'ai_reverse'和'liq_magnet_reversal'错误模式设计。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reverse_strength",
            name="Short-term Reversal Strength",
            display_name="短期反转强度",
            description="基于短期均线与长期均线的乖离率以及价格相对布林带上轨的位置，识别极端超买超卖后的反转概率。当乖离率过大且触及布林带上轨时，短期反转向下概率高；乖离率过小且触及下轨时反转向上概率高。因子值正向表示预期向上反转，负向表示向下反转。此因子针对'ai_reverse'和'liq_magnet_reversal'错误模式设计。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        fast = 5
        slow = 20
        # 均线
        ma_fast = df['close'].rolling(fast).mean()
        ma_slow = df['close'].rolling(slow).mean()
        # 乖离率
        bais = (ma_fast - ma_slow) / (ma_slow + 1e-10)
        # 布林带
        std = df['close'].rolling(slow).std()
        upper = ma_slow + 2 * std
        lower = ma_slow - 2 * std
        # 价格位置：0~1之间（上轨=1，下轨=0）
        bb_pos = (df['close'] - lower) / (upper - lower + 1e-10)
        # 合成信号：乖离率大且在上轨附近 => 向下反转；乖离率小且在下轨附近 => 向上反转
        signal = -bais * (bb_pos - 0.5) * 2  # 正乖离+上轨 => 负值，负乖离+下轨 => 正值
        # 归一化到[-1,1]
        result = signal.rolling(5).mean().fillna(0).clip(-1, 1)
        return result
