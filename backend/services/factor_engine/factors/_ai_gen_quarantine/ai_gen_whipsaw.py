"""AI因子: 价格震荡指数 | 置信:60% | 捕捉价格频繁反向波动（假突破）的程度。通过计算连续两日收盘价方向变化的频率和幅度，当价格反复穿越短期均线时值接近+1，预示容易触发止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceWhipsawIndex(BaseFactor):
    """捕捉价格频繁反向波动（假突破）的程度。通过计算连续两日收盘价方向变化的频率和幅度，当价格反复穿越短期均线时值接近+1，预示容易触发止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_whipsaw",
            name="Price Whipsaw Index",
            display_name="价格震荡指数",
            description="捕捉价格频繁反向波动（假突破）的程度。通过计算连续两日收盘价方向变化的频率和幅度，当价格反复穿越短期均线时值接近+1，预示容易触发止损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 短期价格方向：1上涨，-1下跌
        direction = np.sign(data['close'].diff())
        # 方向变化次数（震荡次数）
        change = (direction.diff() != 0).astype(int)
        # 近10日平均每根K线方向变化次数
        whip_count = change.rolling(10).mean()
        # 同时考虑价格振幅与均线偏离
        ma5 = data['close'].rolling(5).mean()
        ma20 = data['close'].rolling(20).mean()
        # 短期价格与长期均线的距离标准化
        dist = abs(data['close'] - ma20) / (ma20 + 1e-8)
        # 综合：震荡次数多且价格离均线近（横盘）则更可能是假突破
        raw = whip_count * (1 - dist * 5)  # dist一般小于0.1，所以1-dist*5在0.5附近
        # 滚动归一化到[-1,1]
        min_val = raw.rolling(60).min()
        max_val = raw.rolling(60).max()
        result = -1 + 2 * (raw - min_val) / (max_val - min_val + 1e-8)
        result = result.fillna(0)
        # 限幅
        result = np.clip(result, -1, 1)
        return result
