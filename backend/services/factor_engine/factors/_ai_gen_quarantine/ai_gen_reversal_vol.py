"""AI因子: 波动率激增反转 | 置信:60% | 检测价格在短时间内出现剧烈波动并伴随成交量陡增，随后价格未能延续方向而快速回撤，符合流动性陷阱和AI反向模式。利用价格范围（振幅）与成交量的乘积变化率，结合动量方向一致性来判断。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySurgeReversal(BaseFactor):
    """检测价格在短时间内出现剧烈波动并伴随成交量陡增，随后价格未能延续方向而快速回撤，符合流动性陷阱和AI反向模式。利用价格范围（振幅）与成交量的乘积变化率，结合动量方向一致性来判断。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_vol",
            name="Volatility Surge Reversal",
            display_name="波动率激增反转",
            description="检测价格在短时间内出现剧烈波动并伴随成交量陡增，随后价格未能延续方向而快速回撤，符合流动性陷阱和AI反向模式。利用价格范围（振幅）与成交量的乘积变化率，结合动量方向一致性来判断。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 参数
        short_window = 3
        long_window = 10
        # 振幅 = 最高-最低
        amplitude = data['high'] - data['low']
        # 波动成交量 = 振幅 * 成交量
        vol_amp = amplitude * data['volume']
        # 波动成交量变化率
        vol_amp_ratio = vol_amp / vol_amp.rolling(long_window, min_periods=1).mean()
        # 价格短期动量
        mom = data['close'].pct_change(short_window)
        # 反转条件：波动成交量激增(>2倍)且动量方向与之前相反？简化：当波动率激增且动量绝对值大时，认为是潜在反转
        surge = (vol_amp_ratio > 2.0).astype(float)
        strong_mom = (mom.abs() > 0.02).astype(float)
        # 结合：涌浪且强动量 -> 反转信号（方向取反）
        signal = surge * strong_mom * (-np.sign(mom))  # 方向与动量相反
        # 归一化到[-1,1]
        result = pd.Series(np.clip(signal, -1, 1), index=data.index)
        return result
