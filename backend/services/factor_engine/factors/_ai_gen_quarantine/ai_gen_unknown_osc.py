"""AI因子: 未知状态震荡因子 | 置信:60% | 通过比较价格动量与ATR偏离度，识别无明显趋势的震荡环境，即regime=unknown状态。计算20周期ATR与20周期价格变动绝对值的比值，再乘以短期动量方向（正负符号），最终用tanh压缩到[-1,1]。正值表示上行震荡，负值表示下行震荡，接近0表示趋势清晰。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeOscillator(BaseFactor):
    """通过比较价格动量与ATR偏离度，识别无明显趋势的震荡环境，即regime=unknown状态。计算20周期ATR与20周期价格变动绝对值的比值，再乘以短期动量方向（正负符号），最终用tanh压缩到[-1,1]。正值表示上行震荡，负值表示下行震荡，接近0表示趋势清晰。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_osc",
            name="Unknown_Regime_Oscillator",
            display_name="未知状态震荡因子",
            description="通过比较价格动量与ATR偏离度，识别无明显趋势的震荡环境，即regime=unknown状态。计算20周期ATR与20周期价格变动绝对值的比值，再乘以短期动量方向（正负符号），最终用tanh压缩到[-1,1]。正值表示上行震荡，负值表示下行震荡，接近0表示趋势清晰。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # ATR
        prev_close = close.shift(1)
        tr = pd.concat([high - low, abs(high - prev_close), abs(low - prev_close)], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        # 价格变化绝对值
        price_change = close.diff(20).abs()
        # 震荡比率：ATR相对于价格变化的比例，越大越震荡
        osc_ratio = atr / (price_change + 1e-10)
        # 短期动量方向
        momentum = close.diff(3)
        direction = np.sign(momentum)
        # 组合：震荡程度乘方向，接近0表示趋势明确
        raw = osc_ratio * direction
        # 压缩到[-1,1]
        result = np.tanh(raw - 1)  # 中心化
        return result.fillna(0)
