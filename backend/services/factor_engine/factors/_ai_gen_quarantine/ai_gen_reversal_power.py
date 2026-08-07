"""AI因子: 反转力量因子 | 置信:55% | 基于短期价格位置和波动率识别强力反转信号。通过计算当前收盘价相对于过去N天最高价和最低价的位置（RSI风格），并结合ATR放大信号强度。当价格接近近期高点且出现快速回调时，输出负值（多头风险）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalStrength(BaseFactor):
    """基于短期价格位置和波动率识别强力反转信号。通过计算当前收盘价相对于过去N天最高价和最低价的位置（RSI风格），并结合ATR放大信号强度。当价格接近近期高点且出现快速回调时，输出负值（多头风险）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_power",
            name="ReversalStrength",
            display_name="反转力量因子",
            description="基于短期价格位置和波动率识别强力反转信号。通过计算当前收盘价相对于过去N天最高价和最低价的位置（RSI风格），并结合ATR放大信号强度。当价格接近近期高点且出现快速回调时，输出负值（多头风险）。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        window = 14
        # 最高价和最低价
        max_high = high.rolling(window, min_periods=window).max()
        min_low = low.rolling(window, min_periods=window).min()
        # 计算位置指标（类似RSI分母）
        upside = close - min_low
        downside = max_high - min_low
        # 避免除以0
        ratio = upside / (downside + 1e-10)
        # 接近1表示接近高点，接近0表示接近低点
        # 使用ATR进行加权
        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        atr = tr.rolling(window, min_periods=window).mean()
        # 当前价格变化幅度
        ret = close.pct_change()
        # 反转信号：当ratio>0.8（接近顶部）且ret为负时，表示可能反转下跌，输出负值
        # 构建一个符号函数
        signal = np.where((ratio > 0.8) & (ret < 0), -1, 0)
        # 加入ATR调整强度：ATR越大，反转力度越强
        atr_norm = atr / close  # 相对ATR
        strength = atr_norm * 5  # 缩放
        # 限制在[-1,1]
        result = np.clip(signal * strength, -1, 1)
        return pd.Series(result, index=data.index).fillna(0)
