"""AI因子: 波动率收缩假突破因子 | 置信:60% | 检测布林带宽度收窄至近期低位后，价格突破上下轨但成交量未有效放大且迅速回归的假突破模式。输出负值代表向上假突破风险（多头陷阱，避免做多），正值代表向下假突破风险（空头陷阱，避免做空），帮助过滤低质量突破信号导致的超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueezeFakeout(BaseFactor):
    """检测布林带宽度收窄至近期低位后，价格突破上下轨但成交量未有效放大且迅速回归的假突破模式。输出负值代表向上假突破风险（多头陷阱，避免做多），正值代表向下假突破风险（空头陷阱，避免做空），帮助过滤低质量突破信号导致的超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vsf",
            name="Volatility Squeeze Fakeout",
            display_name="波动率收缩假突破因子",
            description="检测布林带宽度收窄至近期低位后，价格突破上下轨但成交量未有效放大且迅速回归的假突破模式。输出负值代表向上假突破风险（多头陷阱，避免做多），正值代表向下假突破风险（空头陷阱，避免做空），帮助过滤低质量突破信号导致的超时亏损。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 布林带 20,2
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        bb_width = upper - lower
        # 宽度历史低点（50期）
        width_percentile = bb_width.rolling(50).rank(pct=True)
        squeeze = width_percentile < 0.2
        # 突破条件
        break_up = (close > upper) & squeeze
        break_down = (close < lower) & squeeze
        # 成交量条件：突破K线成交量低于20期均量
        vol_ma = volume.rolling(20).mean()
        low_vol = volume < vol_ma
        # 回归条件：突破后下一根K线收盘价回到布林带内
        return_inside_after_up = break_up.shift(1) & (close < upper)
        return_inside_after_down = break_down.shift(1) & (close > lower)
        # 假突破信号
        fake_up = break_up & low_vol & return_inside_after_up.shift(-1).fillna(False)
        fake_down = break_down & low_vol & return_inside_after_down.shift(-1).fillna(False)
        result = pd.Series(0.0, index=data.index)
        # 计算强度：突破幅度与回归幅度
        strength_up = ((upper - close) / std).clip(0, 1)
        strength_down = ((close - lower) / std).clip(0, 1)
        result[fake_up] = -strength_up[fake_up]
        result[fake_down] = strength_down[fake_down]
        # 将信号向前填充1期，表示在突破发生时即可获得风险值
        result = result.shift(-1).fillna(0)
        result = result.rolling(3).mean().fillna(0)
        return result.clip(-1, 1)
