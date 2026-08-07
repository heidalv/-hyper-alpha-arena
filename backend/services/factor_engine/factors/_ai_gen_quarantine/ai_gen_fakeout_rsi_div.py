"""AI因子: RSI顶背离检测 | 置信:65% | 当价格创近期新高但RSI未能同步创高时，预示上涨动能衰竭、可能发生趋势反转或假突破。亏损记录中大量master_running和max_hold_timeout表明持仓在趋势衰竭后回落，该因子对这种环境给出负值信号，避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSIBearishDivergenceDetector(BaseFactor):
    """当价格创近期新高但RSI未能同步创高时，预示上涨动能衰竭、可能发生趋势反转或假突破。亏损记录中大量master_running和max_hold_timeout表明持仓在趋势衰竭后回落，该因子对这种环境给出负值信号，避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fakeout_rsi_div",
            name="RSI Bearish Divergence Detector",
            display_name="RSI顶背离检测",
            description="当价格创近期新高但RSI未能同步创高时，预示上涨动能衰竭、可能发生趋势反转或假突破。亏损记录中大量master_running和max_hold_timeout表明持仓在趋势衰竭后回落，该因子对这种环境给出负值信号，避免做多。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        # 计算RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        # 寻找20周期内的价格高点和对应RSI高点
        price_high = close.rolling(20).max()
        rsi_high = rsi.rolling(20).max()
        # 当前价格接近20期高点，但RSI未接近其20期高点，形成背离
        price_near_high = (close >= price_high * 0.98).astype(float)
        rsi_not_high = (rsi <= rsi_high * 0.95).astype(float)
        divergence_signal = price_near_high * rsi_not_high
        # 平滑并映射到[-1,1]，负值表示背离风险高
        result = -1.0 * divergence_signal.rolling(3).mean().fillna(0).clip(-1, 1)
        return result
