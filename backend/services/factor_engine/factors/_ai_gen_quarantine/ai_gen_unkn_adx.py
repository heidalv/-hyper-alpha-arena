"""AI因子: 弱趋势指示器 | 置信:60% | 使用类似ADX的指标衡量趋势强度。当ADX低于阈值（默认20）时，市场无明显趋势，容易出现超时止损等亏损模式。因子值为 -1 当ADX极低，+1 当ADX极高，线性映射。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WeakTrendIndicator(BaseFactor):
    """使用类似ADX的指标衡量趋势强度。当ADX低于阈值（默认20）时，市场无明显趋势，容易出现超时止损等亏损模式。因子值为 -1 当ADX极低，+1 当ADX极高，线性映射。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unkn_adx",
            name="WeakTrendIndicator",
            display_name="弱趋势指示器",
            description="使用类似ADX的指标衡量趋势强度。当ADX低于阈值（默认20）时，市场无明显趋势，容易出现超时止损等亏损模式。因子值为 -1 当ADX极低，+1 当ADX极高，线性映射。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算真实波幅TR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift(1))
        low_close = np.abs(data['low'] - data['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算方向移动
        up_move = data['high'] - data['high'].shift(1)
        down_move = data['low'].shift(1) - data['low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0).astype(float)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0).astype(float)
        plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / atr
        # 计算DX
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()
        # 映射到[-1,1]，ADX范围0-100，通常20以下弱趋势
        factor = -1 + 2 * np.clip((adx - 20) / 80, 0, 1)  # ADX=20时factor=-1，ADX=100时factor=1
        factor = factor.fillna(0)
        return factor
