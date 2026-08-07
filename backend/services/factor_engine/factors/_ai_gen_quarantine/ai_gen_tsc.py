"""AI因子: 趋势强度置信度 | 置信:70% | 通过对比短期和长期ADX值判断当前是否为无明显趋势的震荡区间。当ADX低于阈值且短期ADX低于长期ADX时，市场处于regime=unknown状态，此时避免趋势跟踪，采用反向交易信号。信号值为0表示不交易，正值表示看多，负值表示看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthConfidence(BaseFactor):
    """通过对比短期和长期ADX值判断当前是否为无明显趋势的震荡区间。当ADX低于阈值且短期ADX低于长期ADX时，市场处于regime=unknown状态，此时避免趋势跟踪，采用反向交易信号。信号值为0表示不交易，正值表示看多，负值表示看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tsc",
            name="TrendStrengthConfidence",
            display_name="趋势强度置信度",
            description="通过对比短期和长期ADX值判断当前是否为无明显趋势的震荡区间。当ADX低于阈值且短期ADX低于长期ADX时，市场处于regime=unknown状态，此时避免趋势跟踪，采用反向交易信号。信号值为0表示不交易，正值表示看多，负值表示看空。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ADX
        high = data['high']
        low = data['low']
        close = data['close']

        # 计算+DI和-DI
        prev_close = close.shift(1)
        prev_high = high.shift(1)
        prev_low = low.shift(1)

        up_move = high - prev_high
        down_move = prev_low - low

        pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

        tr = np.maximum(high - low, np.abs(high - prev_close), np.abs(low - prev_close))
        atr = tr.rolling(14).mean()

        pos_di = 100 * pd.Series(pos_dm, index=data.index).rolling(14).mean() / (atr + 1e-9)
        neg_di = 100 * pd.Series(neg_dm, index=data.index).rolling(14).mean() / (atr + 1e-9)

        dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di + 1e-9)
        adx = dx.rolling(14).mean()
        adx_short = dx.rolling(6).mean()

        # 判定震荡：ADX < 25 且 短期ADX < 长期ADX
        regime_unknown = (adx < 25) & (adx_short < adx)

        # 在震荡区间内，使用均值回复信号：价格远离均线则反向
        ma_short = close.rolling(20).mean()
        deviation = (close - ma_short) / (close.rolling(20).std() + 1e-9)

        signal = np.where(regime_unknown, -np.sign(deviation) * 0.5, 0)
        return pd.Series(signal, index=data.index).clip(-1, 1)
