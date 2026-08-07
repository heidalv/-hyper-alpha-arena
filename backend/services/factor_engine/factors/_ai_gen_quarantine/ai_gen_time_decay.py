"""AI因子: 持仓时间压力因子 | 置信:55% | 模拟长时间持仓导致的亏损模式：当价格在一个窄幅区间内长时间震荡（高波动率萎缩），且成交量递减，随后可能出现突破失败而反向运行。利用布林带宽收缩和成交量递减来预警。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimePressureFactor(BaseFactor):
    """模拟长时间持仓导致的亏损模式：当价格在一个窄幅区间内长时间震荡（高波动率萎缩），且成交量递减，随后可能出现突破失败而反向运行。利用布林带宽收缩和成交量递减来预警。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_time_decay",
            name="Hold Time Pressure Factor",
            display_name="持仓时间压力因子",
            description="模拟长时间持仓导致的亏损模式：当价格在一个窄幅区间内长时间震荡（高波动率萎缩），且成交量递减，随后可能出现突破失败而反向运行。利用布林带宽收缩和成交量递减来预警。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 布林带带宽收缩
        ma20 = data['close'].rolling(20).mean()
        std20 = data['close'].rolling(20).std()
        upper = ma20 + 2*std20
        lower = ma20 - 2*std20
        bandwidth = (upper - lower) / ma20
        # 成交量递减：5日均量连续下降
        vol_ma5 = data['volume'].rolling(5).mean()
        vol_decline = vol_ma5 < vol_ma5.shift(1)
        # 带宽收缩到过去100天最小值附近
        band_min = bandwidth.rolling(100).min()
        band_shrink = bandwidth <= band_min * 1.1
        # 价格在布林带内部（没有突破）
        inside_band = (data['close'] > lower) & (data['close'] < upper)
        # 综合信号：窄幅震荡+成交量递减+在带内 => 可能反转
        signal = np.where(inside_band & band_shrink & vol_decline, 1, 0)  # 信号方向待定，此处取绝对值
        # 进一步通过短期动量判断方向：若最近3天收盘价下跌则做多，反之做空
        price_change = data['close'].diff(3)
        direction = np.sign(price_change)
        result = pd.Series(signal * direction, index=data.index).clip(-1,1)
        return result
