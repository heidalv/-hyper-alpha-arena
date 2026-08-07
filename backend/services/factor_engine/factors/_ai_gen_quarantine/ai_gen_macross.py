"""AI因子: 均线回归交叉强度 | 置信:60% | 基于快慢均线交叉的强度和价格偏离度，判断当前是否适合趋势跟踪或均值回归。当价格远离均线且交叉力度弱时，regime不明，因子值接近0；强趋势下接近+1；强回归机会下接近-1。帮助避免在unknown状态下开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Cross_Strength(BaseFactor):
    """基于快慢均线交叉的强度和价格偏离度，判断当前是否适合趋势跟踪或均值回归。当价格远离均线且交叉力度弱时，regime不明，因子值接近0；强趋势下接近+1；强回归机会下接近-1。帮助避免在unknown状态下开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_macross",
            name="Mean Reversion Cross Strength",
            display_name="均线回归交叉强度",
            description="基于快慢均线交叉的强度和价格偏离度，判断当前是否适合趋势跟踪或均值回归。当价格远离均线且交叉力度弱时，regime不明，因子值接近0；强趋势下接近+1；强回归机会下接近-1。帮助避免在unknown状态下开仓。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close'].values
        ma_fast = pd.Series(close).rolling(10).mean().values
        ma_slow = pd.Series(close).rolling(30).mean().values
        # 均线差值归一化
        diff = (ma_fast - ma_slow) / (close + 1e-8)
        # 价格偏离均线的程度
        dev = (close - ma_fast) / (close + 1e-8)
        # 计算交叉动量
        cross_momentum = np.diff(np.concatenate([[0], diff]))
        # 强度因子：diff符号与dev一致代表趋势，否则回归
        strength = np.sign(diff) * np.sign(dev) * np.abs(diff)
        # 结合交叉动量调整
        adjusted = strength * np.clip(np.abs(cross_momentum) * 10, 0, 1)
        # 滚动平均平滑
        result = pd.Series(adjusted).rolling(5, min_periods=1).mean().fillna(0)
        return result.clip(-1, 1)
