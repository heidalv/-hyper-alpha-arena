"""AI因子: 自适应趋势强度 | 置信:70% | 基于类似ADX原理的简化趋势强度指标，通过比较价格方向移动（正负价格变动幅度）与波动率，判断市场是否处于强趋势或无序盘整。ADX低值（<25）对应未知状态，映射到接近-1；ADX高值（>50）映射到+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AdaptiveTrendIntensity(BaseFactor):
    """基于类似ADX原理的简化趋势强度指标，通过比较价格方向移动（正负价格变动幅度）与波动率，判断市场是否处于强趋势或无序盘整。ADX低值（<25）对应未知状态，映射到接近-1；ADX高值（>50）映射到+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adi",
            name="Adaptive Trend Intensity",
            display_name="自适应趋势强度",
            description="基于类似ADX原理的简化趋势强度指标，通过比较价格方向移动（正负价格变动幅度）与波动率，判断市场是否处于强趋势或无序盘整。ADX低值（<25）对应未知状态，映射到接近-1；ADX高值（>50）映射到+1。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 计算简化版的+DI和-DI：基于每日价格变化的正负绝对值
        high = data['high']
        low = data['low']
        close = data['close']
        # 方向性运动
        up_move = high.diff()
        down_move = -low.diff()
        up_move = up_move.clip(lower=0)
        down_move = down_move.clip(lower=0)
        # 真实波幅
        tr = pd.concat([high - low,
                        (high - close.shift(1)).abs(),
                        (low - close.shift(1)).abs()], axis=1).max(axis=1)
        # 平滑周期
        period = 14
        # 使用EMA平滑（此处为了简化用滚动总和）
        smooth_tr = tr.rolling(window=period, min_periods=1).sum()
        smooth_up = up_move.rolling(window=period, min_periods=1).sum()
        smooth_down = down_move.rolling(window=period, min_periods=1).sum()
        # 计算+DI和-DI
        plus_di = 100 * smooth_up / (smooth_tr + 1e-10)
        minus_di = 100 * smooth_down / (smooth_tr + 1e-10)
        # 计算DX并平滑得ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(window=period, min_periods=1).mean()
        # 将ADX映射到[-1,1]：ADX<25 -> -1，ADX>50 -> 1，中间线性
        result = (adx - 25) / 25  # 当adx=25时为0，adx=50时为1
        result = result.clip(-1, 1)
        result = result.fillna(0)
        return result
