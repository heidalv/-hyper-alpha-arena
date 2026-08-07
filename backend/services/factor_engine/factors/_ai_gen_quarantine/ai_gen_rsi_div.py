"""AI因子: RSI顶背离因子 | 置信:60% | 价格创出新高但RSI指标未能同步创新高，形成顶背离，预示上涨趋势可能结束。计算最近N周期内价格新高次数与RSI新高次数的差值，并结合当前RSI值，输出负值表示看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSI_Divergence_Short(BaseFactor):
    """价格创出新高但RSI指标未能同步创新高，形成顶背离，预示上涨趋势可能结束。计算最近N周期内价格新高次数与RSI新高次数的差值，并结合当前RSI值，输出负值表示看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rsi_div",
            name="RSI Divergence Short",
            display_name="RSI顶背离因子",
            description="价格创出新高但RSI指标未能同步创新高，形成顶背离，预示上涨趋势可能结束。计算最近N周期内价格新高次数与RSI新高次数的差值，并结合当前RSI值，输出负值表示看空。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 14
        close = data['close'].values
        delta = np.diff(close)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).rolling(n).mean().values
        avg_loss = pd.Series(loss).rolling(n).mean().values
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # 取最后n个周期内的价格和RSI新高计数
        lookback = 20
        if len(close) < lookback:
            return pd.Series(0.0, index=data.index)
        price_highs = np.sum(np.maximum.accumulate(close[-lookback:]) == close[-lookback:])
        rsi_highs = np.sum(np.maximum.accumulate(rsi[-lookback:]) == rsi[-lookback:])
        # 如果价格新高次数 > RSI新高次数，视为背离
        divergence = int(price_highs > rsi_highs)
        # 结合当前RSI值（>70超买）强化信号
        rsi_current = rsi[-1] if len(rsi) > 0 else 50
        raw = -divergence * (rsi_current / 100.0)
        result = np.clip(raw * 2 - 0.5, -1, 1)
        return pd.Series(result, index=data.index[-1:], name='rsi_div')
