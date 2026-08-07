"""AI因子: 趋势模糊度指数 | 置信:65% | 基于ADX（平均趋向指数）和价格波动率变异系数，衡量当前市场是否处于无明显趋势的震荡或未知状态。ADX低于20且波动率变异系数较高时，趋势模糊，做多风险大。因子输出负值（-1到0）表示规避做多，正值（0到1）表示趋势明确。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Uncertainty_Index(BaseFactor):
    """基于ADX（平均趋向指数）和价格波动率变异系数，衡量当前市场是否处于无明显趋势的震荡或未知状态。ADX低于20且波动率变异系数较高时，趋势模糊，做多风险大。因子输出负值（-1到0）表示规避做多，正值（0到1）表示趋势明确。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trad_uncertainty",
            name="Trend Uncertainty Index",
            display_name="趋势模糊度指数",
            description="基于ADX（平均趋向指数）和价格波动率变异系数，衡量当前市场是否处于无明显趋势的震荡或未知状态。ADX低于20且波动率变异系数较高时，趋势模糊，做多风险大。因子输出负值（-1到0）表示规避做多，正值（0到1）表示趋势明确。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算14日ADX
        period = 14
        high = data['high']
        low = data['low']
        close = data['close']
        # TR
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # +DM, -DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_dm = pd.Series(plus_dm, index=data.index)
        minus_dm = pd.Series(minus_dm, index=data.index)
        # 平滑
        plus_di = (plus_dm.rolling(period).sum() / atr) * 100
        minus_di = (minus_dm.rolling(period).sum() / atr) * 100
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        adx = dx.rolling(period).mean()
        # 波动率变异系数：过去20日收盘价标准差/均值
        vol = close.rolling(20).std()
        vol_mean = close.rolling(20).mean()
        cv = vol / vol_mean
        # 综合指标：当ADX<20且CV>0.05时，趋势模糊，因子为负
        fuzzy = ((adx < 20) & (cv > 0.05)).astype(int)
        # 归一化到[-1,1]，模糊时-1，清晰时+1
        result = 1 - 2 * fuzzy
        # 处理NaN
        result = result.fillna(0)
        return result
