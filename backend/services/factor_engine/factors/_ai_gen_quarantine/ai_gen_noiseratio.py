"""AI因子: 噪声比率因子 | 置信:60% | 度量价格运动的噪声程度，即单位趋势中的反向波动大小。噪声高时市场方向不明，适合空仓或短线反向交易。使用收盘价线性回归的R方来衡量，R方低则噪声高，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NoiseRatio(BaseFactor):
    """度量价格运动的噪声程度，即单位趋势中的反向波动大小。噪声高时市场方向不明，适合空仓或短线反向交易。使用收盘价线性回归的R方来衡量，R方低则噪声高，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noiseratio",
            name="NoiseRatio",
            display_name="噪声比率因子",
            description="度量价格运动的噪声程度，即单位趋势中的反向波动大小。噪声高时市场方向不明，适合空仓或短线反向交易。使用收盘价线性回归的R方来衡量，R方低则噪声高，输出负值。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算20周期R方
        def rolling_r2(series, window=20):
            def r2_func(arr):
                x = np.arange(len(arr))
                y = arr
                if np.std(y) == 0:
                    return 0
                corr = np.corrcoef(x, y)[0, 1]
                return corr ** 2
            return series.rolling(window).apply(r2_func, raw=True)
        close = data['close']
        r2 = rolling_r2(close, 20)
        # R方范围0~1，小于0.3为高噪声，大于0.7为强趋势
        result = (r2 - 0.5) / 0.4  # 大致[-1.25,1.25]
        result = np.clip(result, -1, 1)
        return result
