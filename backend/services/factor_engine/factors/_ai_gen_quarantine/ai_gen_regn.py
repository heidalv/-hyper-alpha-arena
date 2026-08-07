"""AI因子: 市场状态不确定因子 | 置信:60% | 基于20周期ADX与ATR的比值，当趋势强度低（ADX<25）且波动率较高（ATR/Close>0.05）时输出负值，提示regime=unknown的高风险状态；反之输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownIndicator(BaseFactor):
    """基于20周期ADX与ATR的比值，当趋势强度低（ADX<25）且波动率较高（ATR/Close>0.05）时输出负值，提示regime=unknown的高风险状态；反之输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regn",
            name="Regime Unknown Indicator",
            display_name="市场状态不确定因子",
            description="基于20周期ADX与ATR的比值，当趋势强度低（ADX<25）且波动率较高（ATR/Close>0.05）时输出负值，提示regime=unknown的高风险状态；反之输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high'].values
        low = data['low'].values
        close = data['close'].values
        period = 20
        tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
        atr = np.concatenate([[np.nan]*period, np.array([np.mean(tr[i-period+1:i+1]) for i in range(period-1, len(tr))])])
        # ADX simplified: mean of directional movement
        up = np.diff(high)
        down = -np.diff(low)
        dm_plus = np.where((up > down) & (up > 0), up, 0)
        dm_minus = np.where((down > up) & (down > 0), down, 0)
        tr_smooth = np.concatenate([[np.nan]*period, np.array([np.mean(tr[i-period+1:i+1]) for i in range(period-1, len(tr))])])
        di_plus = np.where(tr_smooth[1:]!=0, dm_plus/tr_smooth[1:], 0)
        di_minus = np.where(tr_smooth[1:]!=0, dm_minus/tr_smooth[1:], 0)
        dx = np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10) * 100
        adx = np.concatenate([[np.nan]*period, np.array([np.nanmean(dx[i-period+1:i+1]) for i in range(period-1, len(dx))])])
        adx = np.concatenate([[np.nan], adx])[:-1]  # align length
        atr_ratio = atr / close
        # scale to [-1, 1]
        raw = np.where((adx < 25) & (atr_ratio > 0.05), -1.0, 1.0)
        result = pd.Series(raw, index=data.index)
        result.iloc[:period] = np.nan
        return result
