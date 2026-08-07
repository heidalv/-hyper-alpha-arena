"""AI因子: 做空挤压风险指标 | 置信:50% | 结合价格突破布林带上轨、成交量放大和相对强弱指标（RSI>70）三个条件，识别潜在的轧空行情。因子值为+1表示强烈看涨（做空风险极高），0表示中性，-1表示看跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSqueezeRiskIndicator(BaseFactor):
    """结合价格突破布林带上轨、成交量放大和相对强弱指标（RSI>70）三个条件，识别潜在的轧空行情。因子值为+1表示强烈看涨（做空风险极高），0表示中性，-1表示看跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_shortrisk",
            name="Short Squeeze Risk Indicator",
            display_name="做空挤压风险指标",
            description="结合价格突破布林带上轨、成交量放大和相对强弱指标（RSI>70）三个条件，识别潜在的轧空行情。因子值为+1表示强烈看涨（做空风险极高），0表示中性，-1表示看跌。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 布林带（20日，2标准差）
        ma20 = data['close'].rolling(20).mean()
        std20 = data['close'].rolling(20).std()
        upper = ma20 + 2 * std20
        # 价格突破上轨
        above_upper = data['close'] > upper
        # 成交量放大：比过去20日均量高1.5倍
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_spike = data['volume'] > (vol_ma20 * 1.5)
        # RSI(14)
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi_high = rsi > 70
        # 复合信号
        squeeze = above_upper & vol_spike & rsi_high
        result = pd.Series(np.where(squeeze, 1.0, 0.0), index=data.index)
        return result
