"""AI因子: 流动性挤兑反转 | 置信:60% | 识别价格在极端偏离移动平均线后快速回拉并伴随成交量急剧萎缩/放大的模式。计算价格与20日均线的偏离度（百分比），当偏离度超过2个标准差且随后一根K线收盘价向均线回归超过50%的偏离，同时成交量相对前一根缩小或放大（根据不同情况），视为流动性挤兑后的反转。返回[-1,1]，负值表示空头反转信号，正值表示多头反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquiditySqueezeReversal(BaseFactor):
    """识别价格在极端偏离移动平均线后快速回拉并伴随成交量急剧萎缩/放大的模式。计算价格与20日均线的偏离度（百分比），当偏离度超过2个标准差且随后一根K线收盘价向均线回归超过50%的偏离，同时成交量相对前一根缩小或放大（根据不同情况），视为流动性挤兑后的反转。返回[-1,1]，负值表示空头反转信号，正值表示多头反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_sqz",
            name="Liquidity Squeeze Reversal",
            display_name="流动性挤兑反转",
            description="识别价格在极端偏离移动平均线后快速回拉并伴随成交量急剧萎缩/放大的模式。计算价格与20日均线的偏离度（百分比），当偏离度超过2个标准差且随后一根K线收盘价向均线回归超过50%的偏离，同时成交量相对前一根缩小或放大（根据不同情况），视为流动性挤兑后的反转。返回[-1,1]，负值表示空头反转信号，正值表示多头反转信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        ma = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        deviation = (data['close'] - ma) / ma * 100
        # 极端偏离：超过2个标准差
        extreme_up = deviation > 2 * std / ma * 100
        extreme_down = deviation < -2 * std / ma * 100
        # 回拉：当前收盘向均线移动超过偏离幅度的50%
        prev_dev = deviation.shift(1)
        reversion_up = extreme_down.shift(1) & (data['close'] > ma) & (abs(deviation) < abs(prev_dev)*0.5)
        reversion_down = extreme_up.shift(1) & (data['close'] < ma) & (abs(deviation) < abs(prev_dev)*0.5)
        # 成交量条件：缩量或放量? 这里用相对前一根变化，缩量更常见于反转初期
        vol_shrink = data['volume'] < data['volume'].shift(1)
        signal = pd.Series(0.0, index=data.index)
        signal[reversion_up & vol_shrink] = 1.0
        signal[reversion_down & vol_shrink] = -1.0
        return signal
