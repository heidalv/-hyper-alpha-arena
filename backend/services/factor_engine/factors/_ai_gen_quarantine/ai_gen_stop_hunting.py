"""AI因子: 止损猎杀频率指数 | 置信:55% | 通过统计日内价格从极值反转的幅度与ATR的比值，识别市场是否存在频繁的假突破或止损触发风险。当因子值接近-1时，表示近期经常出现价格先突破重要价位后快速反向（类似止损猎杀），应警惕sl止损亏损。正值表示价格延续性较好。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopHuntingFrequencyIndex(BaseFactor):
    """通过统计日内价格从极值反转的幅度与ATR的比值，识别市场是否存在频繁的假突破或止损触发风险。当因子值接近-1时，表示近期经常出现价格先突破重要价位后快速反向（类似止损猎杀），应警惕sl止损亏损。正值表示价格延续性较好。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stop_hunting",
            name="Stop Hunting Frequency Index",
            display_name="止损猎杀频率指数",
            description="通过统计日内价格从极值反转的幅度与ATR的比值，识别市场是否存在频繁的假突破或止损触发风险。当因子值接近-1时，表示近期经常出现价格先突破重要价位后快速反向（类似止损猎杀），应警惕sl止损亏损。正值表示价格延续性较好。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        open_ = data['open']
        # 计算上下影线长度相对于ATR的比例
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 上影线：最高点与收盘/开盘较高者之差；下影线：最低点与收盘/开盘较低者之差
        upper_shadow = high - pd.concat([close, open_], axis=1).max(axis=1)
        lower_shadow = pd.concat([close, open_], axis=1).min(axis=1) - low
        # 影线总长度占ATR比例
        shadow_ratio = (upper_shadow + lower_shadow) / (atr + 1e-10)
        # 价格收盘方向：若收盘在中间偏下，说明上影线可能是假突破
        close_position = (close - low) / (high - low + 1e-10)
        # 止损猎杀信号：大影线且收盘在区间极端（暗示突破失败）
        hunt_signal = shadow_ratio * (0.5 - close_position.abs())
        # 滚动平均并映射到[-1,1]
        avg_hunt = hunt_signal.rolling(20).mean()
        result = pd.Series( np.clip(avg_hunt * 5, -1, 1), index=close.index )
        return result.fillna(0)
