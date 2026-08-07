"""AI因子: 长影线反转因子 | 置信:60% | 识别K线中上下影线相对于实体的比例，并结合成交量放大的情况。当上影线较长（>实体2倍）且成交量高于前5日均量时，预示顶部反转；同理下影线较长预示底部反转。输出正值为看跌反转信号，负值为看涨反转信号，幅度由影线相对强度和成交量异常度决定。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LongWickVolumeReversal(BaseFactor):
    """识别K线中上下影线相对于实体的比例，并结合成交量放大的情况。当上影线较长（>实体2倍）且成交量高于前5日均量时，预示顶部反转；同理下影线较长预示底部反转。输出正值为看跌反转信号，负值为看涨反转信号，幅度由影线相对强度和成交量异常度决定。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_reversal",
            name="Long Wick Volume Reversal",
            display_name="长影线反转因子",
            description="识别K线中上下影线相对于实体的比例，并结合成交量放大的情况。当上影线较长（>实体2倍）且成交量高于前5日均量时，预示顶部反转；同理下影线较长预示底部反转。输出正值为看跌反转信号，负值为看涨反转信号，幅度由影线相对强度和成交量异常度决定。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 实体和影线
        body = abs(data['close'] - data['open'])
        upper_wick = data['high'] - data[['close','open']].max(axis=1)
        lower_wick = data[['close','open']].min(axis=1) - data['low']
        # 相对影线强度（相对于实体）
        upper_ratio = upper_wick / (body + 1e-10)
        lower_ratio = lower_wick / (body + 1e-10)
        # 成交量放大: 与5日均量比较
        vol_ma5 = data['volume'].rolling(5).mean()
        vol_surge = data['volume'] / (vol_ma5 + 1e-10)
        # 信号: 上影线长且量大 -> 看跌（正）; 下影线长且量大 -> 看涨（负）
        short_signal = (upper_ratio > 2) & (vol_surge > 1.2)
        long_signal = (lower_ratio > 2) & (vol_surge > 1.2)
        # 综合得分
        factor = pd.Series(0, index=data.index)
        # 上影线: 正分，强度由影线倍数和成交量倍数加权
        factor += short_signal.astype(float) * (upper_ratio - 2) * (vol_surge - 1)
        # 下影线: 负分
        factor -= long_signal.astype(float) * (lower_ratio - 2) * (vol_surge - 1)
        # 归一化到[-1,1]
        factor = factor.clip(-3, 3) / 3
        return pd.Series(factor, index=data.index).fillna(0)
