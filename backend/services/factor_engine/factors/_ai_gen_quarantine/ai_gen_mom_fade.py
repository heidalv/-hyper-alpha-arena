"""AI因子: 新高后动能衰竭 | 置信:60% | 当价格创出近期新高但短期动量指标（如RSI或价格变化率）并未同步创高时，表明上涨动能衰竭，容易形成顶部反转。该因子捕捉价格与动量的负向背离。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Fade_after_New_High(BaseFactor):
    """当价格创出近期新高但短期动量指标（如RSI或价格变化率）并未同步创高时，表明上涨动能衰竭，容易形成顶部反转。该因子捕捉价格与动量的负向背离。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_fade",
            name="Momentum Fade after New High",
            display_name="新高后动能衰竭",
            description="当价格创出近期新高但短期动量指标（如RSI或价格变化率）并未同步创高时，表明上涨动能衰竭，容易形成顶部反转。该因子捕捉价格与动量的负向背离。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 计算20日最高价和10日动量
        recent_high = close.rolling(20).max()
        momentum = close.pct_change(10)
        # 当价格创近期新高但动量低于过去20日动量均值时，信号为负
        mom_ma = momentum.rolling(20).mean()
        cond_new_high = close == recent_high
        signal = - (momentum - mom_ma) * cond_new_high.astype(float)
        # 平滑并标准化
        result = signal.rolling(5).mean().clip(-1, 1)
        return result.fillna(0)
