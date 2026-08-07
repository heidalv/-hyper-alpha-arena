"""AI因子: 反转强度 | 置信:55% | 结合价格连续变动与成交量确认，检测当前可能发生的反转信号强度。值接近+1表示强反转看涨（例如下跌后放量反弹），-1表示强延续看跌。适合辅助判断ai_reverse信号的可靠性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalStrength(BaseFactor):
    """结合价格连续变动与成交量确认，检测当前可能发生的反转信号强度。值接近+1表示强反转看涨（例如下跌后放量反弹），-1表示强延续看跌。适合辅助判断ai_reverse信号的可靠性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversalstrength",
            name="Reversal Strength",
            display_name="反转强度",
            description="结合价格连续变动与成交量确认，检测当前可能发生的反转信号强度。值接近+1表示强反转看涨（例如下跌后放量反弹），-1表示强延续看跌。适合辅助判断ai_reverse信号的可靠性。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算短期动量（3日收益率）
        ret3 = close.pct_change(3)
        # 计算成交量变化率（相对20日均量）
        vol_ma20 = volume.rolling(20, min_periods=1).mean()
        vol_ratio = volume / (vol_ma20 + 1e-10)
        # 反转信号：价格下跌（负收益）同时放量 -> 可能底部反转（正值）
        # 价格上涨（正收益）同时放量 -> 可能顶部反转（负值）
        # 用sigmoid归一化
        raw = -ret3 * (vol_ratio - 1.0)  # 下跌时ret3负，负负得正，放量则vol_ratio>1，正数
        # 滚动标准化
        rolling_std = raw.rolling(20, min_periods=5).std()
        rolling_mean = raw.rolling(20, min_periods=5).mean()
        zscore = (raw - rolling_mean) / (rolling_std + 1e-10)
        # 用tanh压缩到[-1,1]
        result = np.tanh(zscore)
        return result
