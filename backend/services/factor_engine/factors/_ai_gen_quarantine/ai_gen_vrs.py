"""AI因子: 波动率体制转换检测器 | 置信:60% | 比较短期波动率与长期波动率水平，衡量当前市场是否进入未知波动体制。高值表示波动率急剧扩张（通常伴随趋势中断），低值表示波动率异常收缩，帮助避免在体制突变时持仓被止损或超时。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeShiftDetector(BaseFactor):
    """比较短期波动率与长期波动率水平，衡量当前市场是否进入未知波动体制。高值表示波动率急剧扩张（通常伴随趋势中断），低值表示波动率异常收缩，帮助避免在体制突变时持仓被止损或超时。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vrs",
            name="Volatility Regime Shift Detector",
            display_name="波动率体制转换检测器",
            description="比较短期波动率与长期波动率水平，衡量当前市场是否进入未知波动体制。高值表示波动率急剧扩张（通常伴随趋势中断），低值表示波动率异常收缩，帮助避免在体制突变时持仓被止损或超时。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 真实波幅
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        # 短期与长期ATR
        short_atr = tr.ewm(span=5, adjust=False).mean()
        long_atr = tr.ewm(span=30, adjust=False).mean()
        # 比率并取对数
        ratio = short_atr / (long_atr + 1e-9)
        log_ratio = np.log(ratio + 1e-9)
        # 归一化到[-1,1]
        result = np.tanh(log_ratio)
        return result.clip(-1, 1)
