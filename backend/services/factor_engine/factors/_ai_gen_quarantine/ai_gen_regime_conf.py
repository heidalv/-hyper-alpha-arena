"""AI因子: 市场状态置信度 | 置信:60% | 综合波动率、趋势强度、成交量稳定性判断市场是否处于已知趋势状态。当波动率异常、趋势弱、成交量离散时置信度低，返回负值；否则正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeConfidence(BaseFactor):
    """综合波动率、趋势强度、成交量稳定性判断市场是否处于已知趋势状态。当波动率异常、趋势弱、成交量离散时置信度低，返回负值；否则正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_conf",
            name="Regime_Confidence",
            display_name="市场状态置信度",
            description="综合波动率、趋势强度、成交量稳定性判断市场是否处于已知趋势状态。当波动率异常、趋势弱、成交量离散时置信度低，返回负值；否则正值。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        atr_norm = atr / close * 100  # 百分比
        # ADX
        plus_dm = np.where((high - high.shift(1)) > (low.shift(1) - low), np.maximum(high - high.shift(1), 0), 0)
        minus_dm = np.where((low.shift(1) - low) > (high - high.shift(1)), np.maximum(low.shift(1) - low, 0), 0)
        tr14 = tr.rolling(14).sum()
        plus_di = 100 * (plus_dm.rolling(14).sum() / tr14)
        minus_di = 100 * (minus_dm.rolling(14).sum() / tr14)
        dx = np.abs(plus_di - minus_di) / (plus_di + minus_di) * 100
        adx = dx.rolling(14).mean()
        # 成交量变异系数
        vol_cv = volume.rolling(20).std() / volume.rolling(20).mean()
        # 组合得分
        score = (adx / 100) * 2 - 1  # ADX 0~100 映射到 -1~1
        score -= (atr_norm.rolling(20).rank(pct=True) * 2 - 1) * 0.3  # 高波动降低得分
        score -= (vol_cv.rolling(20).rank(pct=True) * 2 - 1) * 0.2  # 高变异降低得分
        result = np.clip(score, -1, 1)
        return result
