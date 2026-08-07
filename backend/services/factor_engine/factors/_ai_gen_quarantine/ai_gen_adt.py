"""AI因子: ADX趋势强度 | 置信:60% | 计算平均趋向指数(ADX)以量化市场趋势强度。当ADX处于低位(例如<20)时，市场缺乏方向，容易导致持仓超时亏损，因子输出接近-1；当ADX较高时，趋势明确，输出接近+1。这有助于过滤无趋势环境，减少max_hold_timeout亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ADXTrendStrength(BaseFactor):
    """计算平均趋向指数(ADX)以量化市场趋势强度。当ADX处于低位(例如<20)时，市场缺乏方向，容易导致持仓超时亏损，因子输出接近-1；当ADX较高时，趋势明确，输出接近+1。这有助于过滤无趋势环境，减少max_hold_timeout亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adt",
            name="ADX Trend Strength",
            display_name="ADX趋势强度",
            description="计算平均趋向指数(ADX)以量化市场趋势强度。当ADX处于低位(例如<20)时，市场缺乏方向，容易导致持仓超时亏损，因子输出接近-1；当ADX较高时，趋势明确，输出接近+1。这有助于过滤无趋势环境，减少max_hold_timeout亏损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        tr = pd.DataFrame({'a': high - low, 
                           'b': abs(high - close.shift(1)), 
                           'c': abs(low - close.shift(1))}).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()
        up = high.diff()
        down = -low.diff()
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        plus_di = 100 * pd.Series(plus_dm, index=data.index).ewm(alpha=1/period, adjust=False).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=data.index).ewm(alpha=1/period, adjust=False).mean() / atr
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
        adx = dx.ewm(alpha=1/period, adjust=False).mean()
        low_thresh = 20.0
        high_thresh = 40.0
        result = np.clip((adx - low_thresh) / (high_thresh - low_thresh), -1.0, 1.0)
        return pd.Series(result, index=data.index)
