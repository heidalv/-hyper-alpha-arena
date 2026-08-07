"""AI因子: 微利平仓风险 | 置信:50% | 检测价格接近近期高点且成交量萎缩的情况，这种模式容易导致过早止盈后价格反转。计算当前收盘价相对于过去10周期最高价的百分比位置，并结合过去5周期成交量变化率。若价格在90%以上高位且成交量下降超过20%，则输出负值表示高风险；反之正值表示低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TinyProfitRisk(BaseFactor):
    """检测价格接近近期高点且成交量萎缩的情况，这种模式容易导致过早止盈后价格反转。计算当前收盘价相对于过去10周期最高价的百分比位置，并结合过去5周期成交量变化率。若价格在90%以上高位且成交量下降超过20%，则输出负值表示高风险；反之正值表示低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tpr",
            name="TinyProfitRisk",
            display_name="微利平仓风险",
            description="检测价格接近近期高点且成交量萎缩的情况，这种模式容易导致过早止盈后价格反转。计算当前收盘价相对于过去10周期最高价的百分比位置，并结合过去5周期成交量变化率。若价格在90%以上高位且成交量下降超过20%，则输出负值表示高风险；反之正值表示低风险。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 过去10周期最高价
        high_10 = data['high'].rolling(10, min_periods=1).max()
        # 当前收盘价相对于最高价的位置（0~1）
        price_pos = (data['close'] / high_10).clip(0, 1)
        # 成交量变化率（5周期百分比变化）
        vol_change = data['volume'].pct_change(5).fillna(0)
        # 定义风险信号：价格高位且成交量萎缩
        high_price = (price_pos >= 0.9).astype(float)
        low_vol = (vol_change < -0.2).astype(float)
        risk = -1.0 * high_price * low_vol
        # 其他情况给一个中性偏正信号（轻微正向）
        neutral = 0.2 * (1 - high_price) + 0.3 * (1 - low_vol) * high_price
        result = risk + neutral
        result = result.clip(-1, 1)
        return result.rename('tiny_profit_risk')
