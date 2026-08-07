"""AI因子: 持仓超时风险因子 | 置信:50% | 基于价格在布林带内的位置与带宽变化，识别市场是否处于窄幅震荡状态。当价格长期在中轨附近徘徊且带宽收缩时，容易触发持仓超时亏损。因子输出负值预警，带宽扩张或价格突破时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutRiskFactor(BaseFactor):
    """基于价格在布林带内的位置与带宽变化，识别市场是否处于窄幅震荡状态。当价格长期在中轨附近徘徊且带宽收缩时，容易触发持仓超时亏损。因子输出负值预警，带宽扩张或价格突破时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_risk",
            name="Hold Timeout Risk Factor",
            display_name="持仓超时风险因子",
            description="基于价格在布林带内的位置与带宽变化，识别市场是否处于窄幅震荡状态。当价格长期在中轨附近徘徊且带宽收缩时，容易触发持仓超时亏损。因子输出负值预警，带宽扩张或价格突破时输出正值。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 布林带
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        boll_width = (upper - lower) / ma20 * 100  # 带宽百分比
        # 价格在布林带内的位置（0~1归一化）
        boll_pos = (close - lower) / (upper - lower + 1e-10)
        # 价格偏离中轨的程度
        mid_dev = (close - ma20) / (std20 + 1e-10)
        # 当带宽很窄（<5%）且价格接近中轨（|mid_dev|<1）时，风险高
        # 定义风险分数
        width_norm = (boll_width - 5) / 5  # 假设正常带宽5-10%，中心7.5
        dev_norm = np.abs(mid_dev) / 2  # 0到1之间
        # 风险因子：带宽窄且偏离小 -> 负值
        risk = - (1 - width_norm.clip(0,1)) * (1 - dev_norm.clip(0,1))
        # 加上突破信号：价格突破上下轨时给正
        breakout = ((close > upper) | (close < lower)).astype(float) * 0.8
        result = risk + breakout
        result = np.clip(result, -0.999, 0.999)
        result = pd.Series(result, index=close.index).fillna(0)
        return result
