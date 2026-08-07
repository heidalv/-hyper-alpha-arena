"""AI因子: 趋势强度因子 | 置信:70% | 基于ADX（平均趋向指数）和方向性指标判断趋势强度，仅在强趋势中给出方向信号，弱趋势中保持中性。使用14日周期计算+DI和-DI，以ADX值加权方向差异，输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthFactor(BaseFactor):
    """基于ADX（平均趋向指数）和方向性指标判断趋势强度，仅在强趋势中给出方向信号，弱趋势中保持中性。使用14日周期计算+DI和-DI，以ADX值加权方向差异，输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ts",
            name="Trend Strength Factor",
            display_name="趋势强度因子",
            description="基于ADX（平均趋向指数）和方向性指标判断趋势强度，仅在强趋势中给出方向信号，弱趋势中保持中性。使用14日周期计算+DI和-DI，以ADX值加权方向差异，输出[-1,1]。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算真波幅TR
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        # 计算方向移动
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        # +DM 和 -DM
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 平滑 (14周期)
        period = 14
        tr_smooth = pd.Series(tr).rolling(period).mean()
        plus_smooth = pd.Series(plus_dm).rolling(period).mean()
        minus_smooth = pd.Series(minus_dm).rolling(period).mean()
        # +DI 和 -DI
        plus_di = 100 * plus_smooth / (tr_smooth + 1e-10)
        minus_di = 100 * minus_smooth / (tr_smooth + 1e-10)
        # DX 和 ADX
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean()
        # 方向信号：+DI - -DI 归一化到[-1,1]
        di_diff = (plus_di - minus_di) / 100.0  # 范围约[-1,1]
        # 用ADX加权：ADX>25认为趋势存在，乘以权重
        adx_weight = np.clip((adx - 20) / 20.0, 0, 1)  # 20~40线性映射到0~1
        result = pd.Series(di_diff * adx_weight, index=close.index)
        result = result.fillna(0)
        return result
