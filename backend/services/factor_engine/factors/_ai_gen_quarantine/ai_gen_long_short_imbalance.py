"""AI因子: 多空成交不平衡 | 置信:60% | 基于分钟级Tick级别模拟多空成交量不平衡度：上涨时成交量为买方，下跌为卖方，计算净买方占比。高不平衡但价格未能持续反映预期方向时预示反转（如master_running_close_tiny亏损）。输出[-1,1]表示多头/空头过热。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Long_Short_Volume_Imbalance(BaseFactor):
    """基于分钟级Tick级别模拟多空成交量不平衡度：上涨时成交量为买方，下跌为卖方，计算净买方占比。高不平衡但价格未能持续反映预期方向时预示反转（如master_running_close_tiny亏损）。输出[-1,1]表示多头/空头过热。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_long_short_imbalance",
            name="Long Short Volume Imbalance",
            display_name="多空成交不平衡",
            description="基于分钟级Tick级别模拟多空成交量不平衡度：上涨时成交量为买方，下跌为卖方，计算净买方占比。高不平衡但价格未能持续反映预期方向时预示反转（如master_running_close_tiny亏损）。输出[-1,1]表示多头/空头过热。",
            category="derivatives",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 使用OHLCV近似Tick多空: 上涨bar假定买方主导，下跌卖方主导
        price_change = data['close'] - data['open']
        # 成交量方向代理
        buy_vol = data['volume'] * (price_change > 0).astype(int)
        sell_vol = data['volume'] * (price_change < 0).astype(int)
        # 净买方比例
        net_buy_ratio = (buy_vol - sell_vol) / (data['volume'] + 1e-10)
        # 平滑并缩放
        smoothed = net_buy_ratio.rolling(5).mean()
        # 与价格动量背离：当净买强但价格涨速放缓，预示回调
        ret = data['close'].pct_change(5)
        divergence = smoothed * (1 - ret.abs())  # 动量弱时放大不平衡
        score = divergence.fillna(0).clip(-1, 1)
        return score
