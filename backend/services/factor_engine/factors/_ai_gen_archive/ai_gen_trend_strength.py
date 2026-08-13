"""AI因子: 趋势强度评分 | 置信:60% | 基于ADX思想简化：计算方向移动指数DX，再求平滑ADX。ADX值高表示趋势强，输出正值；低表示震荡/未知状态，输出负值。帮助过滤掉趋势不明确的交易环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthScore(BaseFactor):
    """基于ADX思想简化：计算方向移动指数DX，再求平滑ADX。ADX值高表示趋势强，输出正值；低表示震荡/未知状态，输出负值。帮助过滤掉趋势不明确的交易环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_strength",
            name="Trend Strength Score",
            display_name="趋势强度评分",
            description="基于ADX思想简化：计算方向移动指数DX，再求平滑ADX。ADX值高表示趋势强，输出正值；低表示震荡/未知状态，输出负值。帮助过滤掉趋势不明确的交易环境。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        # 计算+DM和-DM
        up_move = high - high.shift()
        down_move = low.shift() - low
        pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        # 平滑
        tr_smooth = tr.rolling(period).sum()
        pos_dm_smooth = pd.Series(pos_dm, index=data.index).rolling(period).sum()
        neg_dm_smooth = pd.Series(neg_dm, index=data.index).rolling(period).sum()
        # 方向指标
        pdi = 100 * pos_dm_smooth / tr_smooth.replace(0, np.nan)
        ndi = 100 * neg_dm_smooth / tr_smooth.replace(0, np.nan)
        dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
        adx = dx.rolling(period).mean()
        # 映射到[-1,1]，通常ADX>25为强趋势
        score = np.where(adx > 30, 1.0, np.where(adx > 20, 0.5, np.where(adx > 15, -0.5, -1.0)))
        result = pd.Series(score, index=data.index)
        return result
