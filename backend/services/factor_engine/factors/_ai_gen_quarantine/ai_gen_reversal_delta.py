"""AI因子: 反转Delta振荡器 | 置信:65% | 基于多周期价格动量背离与成交量确认的反转强度指标。计算短期与长期收益率差值，当差值从极端值快速回归时触发反转信号。因子值接近+1表示强烈看涨反转，-1表示强烈看跌反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalDeltaOscillator(BaseFactor):
    """基于多周期价格动量背离与成交量确认的反转强度指标。计算短期与长期收益率差值，当差值从极端值快速回归时触发反转信号。因子值接近+1表示强烈看涨反转，-1表示强烈看跌反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_delta",
            name="Reversal Delta Oscillator",
            display_name="反转Delta振荡器",
            description="基于多周期价格动量背离与成交量确认的反转强度指标。计算短期与长期收益率差值，当差值从极端值快速回归时触发反转信号。因子值接近+1表示强烈看涨反转，-1表示强烈看跌反转。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 计算短期动量：1分钟收益率
        short_ret = df['close'].pct_change(1)
        # 计算长期动量：5分钟收益率
        long_ret = df['close'].pct_change(5)
        # 动量差：短期减长期（衡量加速度）
        delta = short_ret - long_ret
        # 对delta进行归一化：滚动窗口的z-score (5周期)
        mean_d = delta.rolling(5).mean()
        std_d = delta.rolling(5).std()
        z = (delta - mean_d) / (std_d + 1e-8)
        # 反转信号：当z绝对值大于2时，认为过度偏离即将反转，取负值
        raw_signal = -np.clip(z, -3, 3) / 3.0
        # 成交量确认：若信号绝对值大但成交量萎缩，降低信心
        vol_ratio = df['volume'] / df['volume'].rolling(5).mean()
        confidence = np.minimum(vol_ratio, 2.0) / 2.0
        result = raw_signal * confidence
        result = result.clip(-1, 1)
        return result
