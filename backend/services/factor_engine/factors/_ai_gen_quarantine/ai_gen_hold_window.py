"""AI因子: 最优持仓窗口指标 | 置信:60% | 基于近期波动率与历史波动率的比率，判断市场是否进入高波动状态，高波动时持仓时间应缩短，因子输出负值表示应减少持仓时间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Optimal_Holding_Window_Indicator(BaseFactor):
    """基于近期波动率与历史波动率的比率，判断市场是否进入高波动状态，高波动时持仓时间应缩短，因子输出负值表示应减少持仓时间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_window",
            name="Optimal Holding Window Indicator",
            display_name="最优持仓窗口指标",
            description="基于近期波动率与历史波动率的比率，判断市场是否进入高波动状态，高波动时持仓时间应缩短，因子输出负值表示应减少持仓时间。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算20日波动率（对数收益率标准差）
        log_ret = np.log(close / close.shift(1)).fillna(0)
        hist_vol = log_ret.rolling(20).std()
        # 计算5日短期波动率
        short_vol = log_ret.rolling(5).std()
        # 波动率比率
        vol_ratio = short_vol / hist_vol.clip(lower=1e-8)
        # 当比率大于1.2时认为高波动，输出负值；小于0.8时低波动输出正值
        score = 1 - vol_ratio
        # 使用tanh平滑到[-1,1]
        result = np.tanh(score * 2)
        return result.fillna(0)
