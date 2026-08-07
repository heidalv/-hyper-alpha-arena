"""AI因子: 反转风险信号 | 置信:70% | 结合RSI和价格与均线的偏离度，当短期RSI进入极端区域（超买>70或超卖<30）且价格远离20日均线超过2个ATR时，认为短期反转风险高，应避免追涨杀跌，因子输出负向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ContrarianReversalRisk(BaseFactor):
    """结合RSI和价格与均线的偏离度，当短期RSI进入极端区域（超买>70或超卖<30）且价格远离20日均线超过2个ATR时，认为短期反转风险高，应避免追涨杀跌，因子输出负向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_contrarian_risk",
            name="contrarian_reversal_risk",
            display_name="反转风险信号",
            description="结合RSI和价格与均线的偏离度，当短期RSI进入极端区域（超买>70或超卖<30）且价格远离20日均线超过2个ATR时，认为短期反转风险高，应避免追涨杀跌，因子输出负向。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        import numpy as np
        import pandas as pd
        # 参数
        rsi_period = 7
        overbought = 70
        oversold = 30
        ma_period = 20
        atr_period = 14
        dev_mult = 2.0

        close = data['close']
        high = data['high']
        low = data['low']

        # RSI计算
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(rsi_period, min_periods=1).mean()
        avg_loss = loss.rolling(rsi_period, min_periods=1).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        # 20日均线
        ma20 = close.rolling(ma_period).mean()
        # ATR
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(atr_period).mean()
        # 价格与均线的距离（以ATR为单位）
        deviation = (close - ma20).abs() / atr

        # 超买超卖且偏离过大
        overbought_signal = (rsi > overbought) & (deviation > dev_mult)
        oversold_signal = (rsi < oversold) & (deviation > dev_mult)

        # 信号：超买时做多风险大（负值），超卖时做空风险大（负值），统一返回负向
        # 超买给出-0.6，超卖也给出-0.6（避免反向追空）
        result = pd.Series(0.0, index=data.index)
        result[overbought_signal] = -0.6
        result[oversold_signal] = -0.6

        # 使用滚动均值平滑避免频繁翻转
        result = result.rolling(2).mean().fillna(0.0)
        return result
