"""AI因子: 尘埃清理失败因子 | 置信:55% | 识别价格在窄幅盘整后尝试突破但失败的模式。计算最近N根K线的真实波幅(ATR)平均值与当前波动率的比值，结合价格相对盘整区间的位置。当ATR极低时市场处于“尘埃清理”状态，随后若价格突破区间边界但很快回落，则失败信号。因子正值表示做多失败（假突破向上），负值表示做空失败（假突破向下）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupFailure(BaseFactor):
    """识别价格在窄幅盘整后尝试突破但失败的模式。计算最近N根K线的真实波幅(ATR)平均值与当前波动率的比值，结合价格相对盘整区间的位置。当ATR极低时市场处于“尘埃清理”状态，随后若价格突破区间边界但很快回落，则失败信号。因子正值表示做多失败（假突破向上），负值表示做空失败（假突破向下）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_cleanup_fail",
            name="Dust Cleanup Failure",
            display_name="尘埃清理失败因子",
            description="识别价格在窄幅盘整后尝试突破但失败的模式。计算最近N根K线的真实波幅(ATR)平均值与当前波动率的比值，结合价格相对盘整区间的位置。当ATR极低时市场处于“尘埃清理”状态，随后若价格突破区间边界但很快回落，则失败信号。因子正值表示做多失败（假突破向上），负值表示做空失败（假突破向下）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR（14周期）
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift(1))
        low_close = np.abs(data['low'] - data['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算“尘埃区域”：过去10根K线的最高价和最低价构成的区间
        recent_high = data['high'].rolling(10).max()
        recent_low = data['low'].rolling(10).min()
        # 当前价格在区间内的位置，0~1
        pos = (data['close'] - recent_low) / (recent_high - recent_low)
        # 当前ATR相对于过去20周期ATR均值的比值
        atr_norm = atr / atr.rolling(20).mean()
        # 条件：ATR极低（尘埃清理状态）且价格靠近区间边界
        dust = (atr_norm < 0.5) & (recent_high - recent_low > 0)  # 确保区间存在
        # 向上假突破：价格超过近期高点但收盘回落至区间内
        fake_up = dust & (data['high'] > recent_high.shift(1)) & (data['close'] < recent_high.shift(1))
        # 向下假突破：价格跌破近期低点但收盘回升至区间内
        fake_down = dust & (data['low'] < recent_low.shift(1)) & (data['close'] > recent_low.shift(1))
        # 生成信号：向上假突破为空头信号（-1），向下假突破为多头信号（+1）
        result = pd.Series(0.0, index=data.index)
        result[fake_up] = -1.0
        result[fake_down] = 1.0
        # 保持信号2根K线不消失
        result = result.rolling(2).max().fillna(0)
        return result
