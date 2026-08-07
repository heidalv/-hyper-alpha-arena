"""AI因子: 趋势模糊度因子 | 置信:60% | 使用类似ADX的思想，计算+DI和-DI差值的绝对值，当趋势方向不明确（差值小）时输出负信号，提示避免长期持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendAmbiguity(BaseFactor):
    """使用类似ADX的思想，计算+DI和-DI差值的绝对值，当趋势方向不明确（差值小）时输出负信号，提示避免长期持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ta",
            name="Trend Ambiguity",
            display_name="趋势模糊度因子",
            description="使用类似ADX的思想，计算+DI和-DI差值的绝对值，当趋势方向不明确（差值小）时输出负信号，提示避免长期持仓。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算方向性指标
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算+DM和-DM
        up_move = high.diff()
        down_move = -low.diff()
        up_move[up_move < 0] = 0
        down_move[down_move < 0] = 0
        # 平滑处理
        period = 14
        up_move_smooth = up_move.rolling(period).sum()
        down_move_smooth = down_move.rolling(period).sum()
        # 真实波幅
        tr = pd.concat([high - low,
                        abs(high - close.shift(1)),
                        abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # 计算+DI和-DI
        plus_di = 100 * up_move_smooth / (atr + 1e-10)
        minus_di = 100 * down_move_smooth / (atr + 1e-10)
        # 方向差值绝对值
        di_diff_abs = abs(plus_di - minus_di)
        # ADX需要再平滑，但这里直接用差值绝对值
        # 将差值标准化到0-100范围，然后映射到[-1,1]
        # 当差值<25时认为趋势模糊，输出负；>25趋势明显输出正
        factor = (di_diff_abs - 25) / 25
        factor = factor.clip(-1, 1)
        factor = factor.fillna(0)
        return factor
