"""AI因子: 反转净额信号 | 置信:35% | 基于日内价格波动与成交量分布，检测可能的反转净额效应。计算当前K线成交量与价格变化方向的背离程度，当成交量增大但价格变化微弱时，暗示市场动能衰竭，可能发生反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseNettingSignal(BaseFactor):
    """基于日内价格波动与成交量分布，检测可能的反转净额效应。计算当前K线成交量与价格变化方向的背离程度，当成交量增大但价格变化微弱时，暗示市场动能衰竭，可能发生反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_net",
            name="Reverse Netting Signal",
            display_name="反转净额信号",
            description="基于日内价格波动与成交量分布，检测可能的反转净额效应。计算当前K线成交量与价格变化方向的背离程度，当成交量增大但价格变化微弱时，暗示市场动能衰竭，可能发生反转。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
    
        # 计算真实范围（波动幅度）
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
    
        # 价格变动百分比
        price_change = close.pct_change()
    
        # 成交量相对波动率：单位波动对应的成交量
        vol_per_tr = volume / (tr + 1e-8)
        vol_per_tr_ma = vol_per_tr.rolling(10).mean()
        vol_per_tr_ratio = vol_per_tr / vol_per_tr_ma
    
        # 价格变化方向：正或负
        price_up = price_change > 0
        price_down = price_change < 0
    
        # 当价格上升但单位波动成交量异常高时，可能卖出压力增大（反转向下）
        neg_signal = price_up & (vol_per_tr_ratio > 1.5)
        # 当价格下降但单位波动成交量异常高时，可能买入支撑出现（反转向上）
        pos_signal = price_down & (vol_per_tr_ratio > 1.5)
    
        # 强度缩放：用vol_per_tr_ratio偏离程度作为信号强度
        result = pd.Series(0.0, index=data.index)
        result[pos_signal] = np.minimum(vol_per_tr_ratio[pos_signal] / 3.0, 1.0)
        result[neg_signal] = -np.minimum(vol_per_tr_ratio[neg_signal] / 3.0, 1.0)
        return result
