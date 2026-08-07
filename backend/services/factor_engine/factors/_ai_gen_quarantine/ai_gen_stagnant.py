"""AI因子: 市场停滞检测器 | 置信:65% | 检测市场是否处于低流动性、价格长时间窄幅横盘的状态。该状态容易导致持仓超时（hold_timeout_review）和假信号。使用价格变动率、成交量变化率和价格区间宽度综合判断。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketStagnationDetector(BaseFactor):
    """检测市场是否处于低流动性、价格长时间窄幅横盘的状态。该状态容易导致持仓超时（hold_timeout_review）和假信号。使用价格变动率、成交量变化率和价格区间宽度综合判断。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stagnant",
            name="Market Stagnation Detector",
            display_name="市场停滞检测器",
            description="检测市场是否处于低流动性、价格长时间窄幅横盘的状态。该状态容易导致持仓超时（hold_timeout_review）和假信号。使用价格变动率、成交量变化率和价格区间宽度综合判断。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 价格变动率：近10日平均绝对回报
        ret = data['close'].pct_change().abs().rolling(10).mean()
        # 成交量变动率：近10日平均成交量变化
        vol_change = (data['volume'] / data['volume'].shift(1)).abs().rolling(10).mean()
        # 价格区间宽度：近10日最高最低差除以收盘价
        price_range = (data['high'].rolling(10).max() - data['low'].rolling(10).min()) / data['close']
        # 综合得分：低回报+低成交量变化+窄区间=停滞
        raw = (1 - ret * 100) * (1 - vol_change) * (1 - price_range * 10)
        # 标准化到[-1,1]，正向表示停滞
        # 使用滚动60分位数
        min_val = raw.rolling(60).min()
        max_val = raw.rolling(60).max()
        result = -1 + 2 * (raw - min_val) / (max_val - min_val + 1e-8)
        result = result.fillna(0)
        return result
