"""AI因子: 清算反转风险 | 置信:60% | 识别价格快速向近期的清算密集区域（如最近N根K线的最高/最低点）移动后出现的反转风险。当价格突破近期极值且成交量异常放大时，判断为高风险反转区域，产生正信号（看多）以规避做空亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidationReversalRisk(BaseFactor):
    """识别价格快速向近期的清算密集区域（如最近N根K线的最高/最低点）移动后出现的反转风险。当价格突破近期极值且成交量异常放大时，判断为高风险反转区域，产生正信号（看多）以规避做空亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lrr",
            name="Liquidation Reversal Risk",
            display_name="清算反转风险",
            description="识别价格快速向近期的清算密集区域（如最近N根K线的最高/最低点）移动后出现的反转风险。当价格突破近期极值且成交量异常放大时，判断为高风险反转区域，产生正信号（看多）以规避做空亏损。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        lookback = 20  # 近期极值窗口
        threshold = 1.5  # 突破幅度阈值（ATR倍数）
        vol_factor = 2.0  # 成交量放大倍数
    
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
    
        # 计算ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
    
        # 近期最高最低
        recent_high = high.rolling(lookback).max()
        recent_low = low.rolling(lookback).min()
    
        # 判断是否突破近期极值并伴随成交量放大
        breakout_up = (close > recent_high.shift(1) + threshold * atr) & (volume > volume.rolling(20).mean() * vol_factor)
        breakout_down = (close < recent_low.shift(1) - threshold * atr) & (volume > volume.rolling(20).mean() * vol_factor)
    
        # 反转信号：突破后反向运行（收盘价回到极值内）
        # 实际上我们希望在突破发生时认为反转风险高，所以给出正信号（看多）以防止做空
        # 对于向上突破，做多风险小，但做空风险大，因此我们更关注向下突破后的反转
        # 但这里统一处理：无论方向，只要突破且放量，就认为反转风险高，给出+1（看多信号）
        # 为了平滑，我们仅对向下突破后产生正信号（即避免做空）
        signal = pd.Series(0.0, index=data.index)
        signal[breakout_down] = 1.0
        # 对向上突破做空也可能亏损？但模式中主要是做空亏损，所以重点防范向下突破后做空
        # 另外，如果向下突破后价格迅速反弹，则反转风险高
        # 我们也可以考虑突破后的后续验证：例如下一根K线收盘价高于突破点
        # 简单版本：仅基于突破时刻
        return signal * 2 - 1  # 映射到[-1,1]
