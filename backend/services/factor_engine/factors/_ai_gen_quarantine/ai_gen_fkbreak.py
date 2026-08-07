"""AI因子: 假突破概率 | 置信:60% | 检测价格是否突破近期高低点后又迅速反转，通过计算突破后的收盘价相对突破时位置的回撤幅度，如果回撤超过阈值则判定为假突破，输出负值提示风险，否则输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class fake_breakout(BaseFactor):
    """检测价格是否突破近期高低点后又迅速反转，通过计算突破后的收盘价相对突破时位置的回撤幅度，如果回撤超过阈值则判定为假突破，输出负值提示风险，否则输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fkbreak",
            name="fake_breakout",
            display_name="假突破概率",
            description="检测价格是否突破近期高低点后又迅速反转，通过计算突破后的收盘价相对突破时位置的回撤幅度，如果回撤超过阈值则判定为假突破，输出负值提示风险，否则输出正值。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        lookback = 20
        high = data['high']
        low = data['low']
        close = data['close']
        recent_high = high.rolling(lookback).max().shift(1)
        recent_low = low.rolling(lookback).min().shift(1)
        # 突破阈值：1% 的缓冲
        break_up = close > recent_high * 1.01
        break_down = close < recent_low * 0.99
        # 计算突破后回撤（假设突破时买入/卖出，看后续价格反向幅度）
        ret = close.pct_change()
        # 用未来1根收盘价判断
        future_ret = ret.shift(-1)
        # 向上假突破：突破后下一根K线收盘价低于当前收盘价（回撤）
        fake_up = break_up & (future_ret < -0.005)
        fake_down = break_down & (future_ret > 0.005)
        fake_score = (fake_up.astype(int) - fake_down.astype(int)).rolling(5).mean()
        result = pd.Series(np.clip(-fake_score * 2, -1, 1), index=data.index)
        return result
