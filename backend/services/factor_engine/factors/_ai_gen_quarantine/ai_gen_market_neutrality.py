"""AI因子: 市场中性陷阱 | 置信:50% | 识别价格在窄幅震荡但成交量突然放大或缩量的阶段，这类市场往往缺乏明确方向，容易因持仓超时或止损触发而亏损。因子通过价格变动率与成交量变动率的背离程度计算，正值表示正常趋势环境，负值表示可能的中性陷阱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Market_Neutrality_Trap(BaseFactor):
    """识别价格在窄幅震荡但成交量突然放大或缩量的阶段，这类市场往往缺乏明确方向，容易因持仓超时或止损触发而亏损。因子通过价格变动率与成交量变动率的背离程度计算，正值表示正常趋势环境，负值表示可能的中性陷阱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_market_neutrality",
            name="Market Neutrality Trap",
            display_name="市场中性陷阱",
            description="识别价格在窄幅震荡但成交量突然放大或缩量的阶段，这类市场往往缺乏明确方向，容易因持仓超时或止损触发而亏损。因子通过价格变动率与成交量变动率的背离程度计算，正值表示正常趋势环境，负值表示可能的中性陷阱。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 价格变动率：用高低价区间百分比变化
        price_range = (data['high'] - data['low']) / data['close'].shift(1)
        price_range = price_range.fillna(0)
        # 成交量变动率：相对于20日均量的变化
        vol_ma = data['volume'].rolling(20).mean()
        vol_change = (data['volume'] - vol_ma) / vol_ma
        vol_change = vol_change.fillna(0)

        # 计算价格与成交量的相关性（滚动5期）
        corr = price_range.rolling(5).corr(vol_change).fillna(0)
        # 当相关性低于阈值（负相关或弱相关）且价格变动率低时，提示陷阱
        low_price_movement = (price_range < price_range.rolling(20).quantile(0.2)).astype(int)
        # 信号：正常相关为正信号，陷阱为负信号
        signal = np.where((corr < 0.3) & (low_price_movement == 1), -1, 1)
        # 平滑处理
        result = pd.Series(signal, index=data.index).rolling(3).mean().fillna(0)
        result = result.clip(-1, 1)
        return result
