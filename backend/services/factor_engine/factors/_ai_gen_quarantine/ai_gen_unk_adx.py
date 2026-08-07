"""AI因子: 未知状态弱趋势强度 | 置信:60% | 基于平均趋向指数（ADX）衡量趋势强度。当ADX低于25时，市场处于无趋势或震荡状态，可能为未知状态，因子值接近-1；当ADX高于25时，趋势明确，因子值接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeWeakTrend(BaseFactor):
    """基于平均趋向指数（ADX）衡量趋势强度。当ADX低于25时，市场处于无趋势或震荡状态，可能为未知状态，因子值接近-1；当ADX高于25时，趋势明确，因子值接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unk_adx",
            name="UnknownRegimeWeakTrend",
            display_name="未知状态弱趋势强度",
            description="基于平均趋向指数（ADX）衡量趋势强度。当ADX低于25时，市场处于无趋势或震荡状态，可能为未知状态，因子值接近-1；当ADX高于25时，趋势明确，因子值接近+1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ADX (14周期)
        period = 14
        high = data['high']
        low = data['low']
        close = data['close']
        # 方向运动
        up_move = high.diff()
        down_move = low.diff()
        # 正方向运动 +DM
        plus_dm = pd.Series(0.0, index=data.index)
        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        # 负方向运动 -DM
        minus_dm = pd.Series(0.0, index=data.index)
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move
        # 真实波幅TR
        tr = pd.concat([
            high - low,
            abs(high - close.shift()),
            abs(low - close.shift())
        ], axis=1).max(axis=1)
        # 平滑（Wilder's moving average）
        def wilder_ema(series, period):
            return series.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        tr_smooth = wilder_ema(tr, period)
        plus_dm_smooth = wilder_ema(plus_dm, period)
        minus_dm_smooth = wilder_ema(minus_dm, period)
        # 方向指标DI
        plus_di = 100 * plus_dm_smooth / tr_smooth
        minus_di = 100 * minus_dm_smooth / tr_smooth
        # 方向差DX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        # ADX
        adx = wilder_ema(dx, period)
        # 映射到[-1,1]：ADX < 25 为弱趋势，值-1；ADX >= 25 线性映射到0~1？实际希望连续
        # 使用 sigmoid 变换：中心在25
        score = (adx - 25) / 10.0  # 大致范围
        score = np.clip(score, -1, 1)
        result = score
        return result
