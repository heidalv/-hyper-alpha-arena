"""AI因子: 极端反转因子 | 置信:60% | 基于布林带上下轨和成交量确认，当价格突破上轨且成交量萎缩、RSI超买时，判断为下跌反转信号（做空）；反之突破下轨且成交量萎缩、RSI超卖时判断为上涨反转信号（做多）。模拟亏损模式中的ai_reverse和止损失败情形。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ExtremeReversalFactor(BaseFactor):
    """基于布林带上下轨和成交量确认，当价格突破上轨且成交量萎缩、RSI超买时，判断为下跌反转信号（做空）；反之突破下轨且成交量萎缩、RSI超卖时判断为上涨反转信号（做多）。模拟亏损模式中的ai_reverse和止损失败情形。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_extreme_rev",
            name="Extreme Reversal Factor",
            display_name="极端反转因子",
            description="基于布林带上下轨和成交量确认，当价格突破上轨且成交量萎缩、RSI超买时，判断为下跌反转信号（做空）；反之突破下轨且成交量萎缩、RSI超卖时判断为上涨反转信号（做多）。模拟亏损模式中的ai_reverse和止损失败情形。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        period = 20
        std_mult = 2.0
        rsi_period = 14
        # 计算布林带
        ma = data['close'].rolling(period).mean()
        std = data['close'].rolling(period).std()
        upper = ma + std_mult * std
        lower = ma - std_mult * std
        # 计算RSI
        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.rolling(rsi_period).mean()
        avg_loss = loss.rolling(rsi_period).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 成交量萎缩：当前成交量低于过去20日均量的80%
        vol_ma = data['volume'].rolling(20).mean()
        vol_shrink = data['volume'] < 0.8 * vol_ma
        # 信号生成
        signal = pd.Series(0.0, index=data.index)
        # 超买且触及上轨且成交量萎缩 -> 做空信号 -1
        cond_sell = (data['close'] >= upper) & (rsi > 70) & vol_shrink
        signal[cond_sell] = -1.0
        # 超卖且触及下轨且成交量萎缩 -> 做多信号 +1
        cond_buy = (data['close'] <= lower) & (rsi < 30) & vol_shrink
        signal[cond_buy] = 1.0
        return signal.shift(1).fillna(0.0).clip(-1, 1)
