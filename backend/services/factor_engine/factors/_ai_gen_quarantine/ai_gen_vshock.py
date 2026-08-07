"""AI因子: 波动率冲击指标 | 置信:60% | 捕捉价格突然出现剧烈波动（类似ATR突然放大）的情况，这类冲击往往导致止损或超时亏损。计算：1. 计算真实波幅TR = max(high-low, |high-prev_close|, |low-prev_close|)；2. 计算ATR(14)作为基准；3. 计算TR/ATR的比值，当比值突然超过阈值时视为冲击。同时结合波动方向（上涨或下跌）加以区分。输出正值表示向上冲击（可能诱多），负值表示向下冲击（可能诱空），绝对值大表示冲击强。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Shock_Indicator(BaseFactor):
    """捕捉价格突然出现剧烈波动（类似ATR突然放大）的情况，这类冲击往往导致止损或超时亏损。计算：1. 计算真实波幅TR = max(high-low, |high-prev_close|, |low-prev_close|)；2. 计算ATR(14)作为基准；3. 计算TR/ATR的比值，当比值突然超过阈值时视为冲击。同时结合波动方向（上涨或下跌）加以区分。输出正值表示向上冲击（可能诱多），负值表示向下冲击（可能诱空），绝对值大表示冲击强。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vshock",
            name="Volatility_Shock_Indicator",
            display_name="波动率冲击指标",
            description="捕捉价格突然出现剧烈波动（类似ATR突然放大）的情况，这类冲击往往导致止损或超时亏损。计算：1. 计算真实波幅TR = max(high-low, |high-prev_close|, |low-prev_close|)；2. 计算ATR(14)作为基准；3. 计算TR/ATR的比值，当比值突然超过阈值时视为冲击。同时结合波动方向（上涨或下跌）加以区分。输出正值表示向上冲击（可能诱多），负值表示向下冲击（可能诱空），绝对值大表示冲击强。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        ratio = tr / atr.replace(0, np.nan)
        # 方向：当前收盘相对于前收盘
        direction = np.sign(close - prev_close).fillna(0)
        shock = ratio * direction
        # 归一化到[-1,1]：一般ratio>3视为极端
        result = np.clip(shock / 3.0, -1, 1)
        return result.fillna(0)
