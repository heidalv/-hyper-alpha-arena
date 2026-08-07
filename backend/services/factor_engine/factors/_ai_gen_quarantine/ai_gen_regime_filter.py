"""AI因子: 市场状态不明过滤器 | 置信:65% | 基于ATR和ADX指标，当市场波动率适中且趋势强度极低（ADX<20）时，判定为regime=unknown状态，此时系统应避免入场，因子返回负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownFilter(BaseFactor):
    """基于ATR和ADX指标，当市场波动率适中且趋势强度极低（ADX<20）时，判定为regime=unknown状态，此时系统应避免入场，因子返回负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_filter",
            name="regime_unknown_filter",
            display_name="市场状态不明过滤器",
            description="基于ATR和ADX指标，当市场波动率适中且趋势强度极低（ADX<20）时，判定为regime=unknown状态，此时系统应避免入场，因子返回负值。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        import numpy as np
        import pandas as pd
        # 计算ATR (14)
        high, low, close = data['high'], data['low'], data['close']
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算ADX (14)
        up = high - high.shift()
        down = low.shift() - low
        dx = pd.DataFrame(index=data.index)
        dx['up'] = np.where((up > down) & (up > 0), up, 0)
        dx['down'] = np.where((down > up) & (down > 0), down, 0)
        tr_smooth = tr.rolling(14).mean()
        up_smooth = dx['up'].rolling(14).mean()
        down_smooth = dx['down'].rolling(14).mean()
        di_plus = 100 * up_smooth / tr_smooth
        di_minus = 100 * down_smooth / tr_smooth
        dx_val = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
        adx = dx_val.rolling(14).mean()
        # 计算波动率归一化: 当前ATR / 最近20日平均收盘价
        avg_close = close.rolling(20).mean()
        atr_ratio = atr / avg_close * 100
        # 规则: ADX < 20 且 ATR比率在[0.5%, 2%]之间视为regime unknown
        condition = (adx < 20) & (atr_ratio > 0.5) & (atr_ratio < 2.0)
        result = pd.Series(index=data.index, dtype=float)
        result[condition] = -0.5
        result[~condition] = 0.0
        # 平滑处理，避免频繁跳变
        result = result.rolling(3).mean().fillna(0.0)
        # 映射到[-1,1]区间，极端情况-1
        return result.clip(-1.0, 0.5)
