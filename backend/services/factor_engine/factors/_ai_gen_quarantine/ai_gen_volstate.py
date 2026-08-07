"""AI因子: 波动状态识别因子 | 置信:60% | 基于近期ATR与价格变化幅度判断市场波动状态。当波动率剧增且价格无明显方向时，标记为未知高风险状态（负值）；趋势明确时标记正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Detector(BaseFactor):
    """基于近期ATR与价格变化幅度判断市场波动状态。当波动率剧增且价格无明显方向时，标记为未知高风险状态（负值）；趋势明确时标记正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volstate",
            name="Volatility Regime Detector",
            display_name="波动状态识别因子",
            description="基于近期ATR与价格变化幅度判断市场波动状态。当波动率剧增且价格无明显方向时，标记为未知高风险状态（负值）；趋势明确时标记正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ATR（14周期）
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 价格变化率
        pct = close.pct_change(5)
        # 价格波动与ATR比值
        volatility_ratio = tr.rolling(5).mean() / atr
        # 当波动率比值大于1.5且价格变化绝对值小于0.02时，认为是未知混乱状态
        signal = -1.0 * ((volatility_ratio > 1.5) & (pct.abs() < 0.02)).astype(float)
        # 否则，根据价格方向赋值
        trend_dir = np.sign(pct).fillna(0)
        result = np.where(signal == -1.0, -1.0, trend_dir)
        # 平滑处理
        result = result.rolling(3, min_periods=1).mean().fillna(0)
        return result.clip(-1.0, 1.0)
