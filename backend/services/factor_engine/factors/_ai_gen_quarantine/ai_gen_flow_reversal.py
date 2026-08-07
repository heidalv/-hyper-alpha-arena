"""AI因子: 量价流反转因子 | 置信:60% | 基于成交量加权价格变化与价格本身变化之间的背离，检测资金流向的突然逆转。当价格朝一个方向运动但成交量加权价格显示相反方向时，预示反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceFlowReversal(BaseFactor):
    """基于成交量加权价格变化与价格本身变化之间的背离，检测资金流向的突然逆转。当价格朝一个方向运动但成交量加权价格显示相反方向时，预示反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_flow_reversal",
            name="Volume-Price Flow Reversal",
            display_name="量价流反转因子",
            description="基于成交量加权价格变化与价格本身变化之间的背离，检测资金流向的突然逆转。当价格朝一个方向运动但成交量加权价格显示相反方向时，预示反转。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
    
        # 典型价格
        tp = (high + low + close) / 3
        # 成交量加权价格变化
        vwap = (tp * volume).rolling(20).sum() / volume.rolling(20).sum()
        # 价格变化
        price_change = close.pct_change(1)
        # 量价流变化（vwap变化）
        vwap_change = vwap.pct_change(1)
    
        # 背离：价格与量价流方向相反
        divergence = -price_change * vwap_change
        # 放大信号：量价流变化绝对值大于价格变化绝对值时更可靠
        strength = abs(vwap_change) - abs(price_change)
        raw = divergence * (1 + strength)
    
        # 横盘过滤
        volatility = close.pct_change(5).rolling(20).std()
        raw = raw.where(volatility > 0.005, 0)
        raw = raw.fillna(0)
    
        # 归一化
        std = raw.rolling(50).std()
        mean = raw.rolling(50).mean()
        normalized = (raw - mean) / (std + 1e-10)
        result = np.clip(normalized, -1, 1)
        return result
