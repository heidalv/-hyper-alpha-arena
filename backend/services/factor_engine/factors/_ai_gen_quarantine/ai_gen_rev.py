"""AI因子: 反转信号强度 | 置信:55% | 结合RSI和成交量确认反转概率。计算14周期RSI，当RSI低于30或高于70时，用成交量异常（当前成交量与过去50周期成交量中位数比值）调整信号强度。RSI<30且成交量放大时预示超卖反转，因子值接近+1（看多）；RSI>70且成交量放大时预示超买反转，因子值接近-1（看空）；否则接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ContrarianReversalSignal(BaseFactor):
    """结合RSI和成交量确认反转概率。计算14周期RSI，当RSI低于30或高于70时，用成交量异常（当前成交量与过去50周期成交量中位数比值）调整信号强度。RSI<30且成交量放大时预示超卖反转，因子值接近+1（看多）；RSI>70且成交量放大时预示超买反转，因子值接近-1（看空）；否则接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev",
            name="Contrarian Reversal Signal",
            display_name="反转信号强度",
            description="结合RSI和成交量确认反转概率。计算14周期RSI，当RSI低于30或高于70时，用成交量异常（当前成交量与过去50周期成交量中位数比值）调整信号强度。RSI<30且成交量放大时预示超卖反转，因子值接近+1（看多）；RSI>70且成交量放大时预示超买反转，因子值接近-1（看空）；否则接近0。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14, min_periods=7).mean()
        avg_loss = loss.rolling(14, min_periods=7).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # 成交量相对水平：当前volume / 50期中位数
        vol_med = volume.rolling(50, min_periods=20).median()
        vol_ratio = volume / (vol_med + 1e-10)
        # 构造反转信号
        # 超卖区域: rsi < 30, 且vol_ratio > 1.2 则看多强
        # 超买区域: rsi > 70, 且vol_ratio > 1.2 则看空强
        # 否则信号弱
        oversold = (rsi < 30).astype(float)
        overbought = (rsi > 70).astype(float)
        vol_confirm = (vol_ratio > 1.2).astype(float)
        # 基础信号：超卖->+1，超买->-1，再乘以成交量确认因子（0-1）
        base_signal = oversold * 1.0 - overbought * 1.0
        # 成交量确认强度：当vol_ratio>1.2时取1，否则线性映射0~1
        confirm = np.clip((vol_ratio - 0.8) / (1.2 - 0.8), 0, 1)
        signal = base_signal * confirm
        signal = signal.fillna(0.0)
        # 限制范围
        signal = np.clip(signal, -1.0, 1.0)
        return pd.Series(signal, index=data.index, name='ai_gen_rev')
