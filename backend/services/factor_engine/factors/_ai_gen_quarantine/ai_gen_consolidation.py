"""AI因子: 盘整陷阱风险 | 置信:55% | 连续多根小实体蜡烛表明市场方向不明，多头持仓易因超时或微小止损亏损。统计近期小实体蜡烛的连续天数，并结合价格在近期高位的位置，负值表示盘整中多头风险升高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ConsolidationTrapRisk(BaseFactor):
    """连续多根小实体蜡烛表明市场方向不明，多头持仓易因超时或微小止损亏损。统计近期小实体蜡烛的连续天数，并结合价格在近期高位的位置，负值表示盘整中多头风险升高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_consolidation",
            name="Consolidation Trap Risk",
            display_name="盘整陷阱风险",
            description="连续多根小实体蜡烛表明市场方向不明，多头持仓易因超时或微小止损亏损。统计近期小实体蜡烛的连续天数，并结合价格在近期高位的位置，负值表示盘整中多头风险升高。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        open_ = data['open']
        high = data['high']
        low = data['low']
        # 计算实体大小
        body = (close - open_).abs()
        tr = high - low
        # 小实体条件：实体/振幅 < 0.3
        small_body = (body / tr.replace(0, 1e-6)) < 0.3
        # 计算连续小实体天数（用cumsum技巧）
        streak = small_body.groupby((small_body != small_body.shift(1)).cumsum()).cumcount() + 1
        streak = streak.where(small_body, 0)
        # 价格在20日高点的相对位置
        high20 = high.rolling(20).max()
        position = (close - high20) / (high20 - low.rolling(20).min()).replace(0, 1e-6)
        # 盘整风险：连续小实体天数多且价格处于相对高位时，多头风险大
        raw = streak * (position - 0.5) * 0.5
        result = -np.tanh(raw)
        return result.fillna(0)
