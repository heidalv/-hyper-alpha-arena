"""AI因子: 市场效率系数 | 置信:65% | 通过比较对数收益率净变化与每日绝对对数收益率之和的比值，度量价格运动的效率。低效率值对应横盘震荡（max_hold_timeout高发区），高效率对应趋势行情。值域[-1,1]，正值表示趋势高效，负值表示震荡低效。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketEfficiencyCoefficient(BaseFactor):
    """通过比较对数收益率净变化与每日绝对对数收益率之和的比值，度量价格运动的效率。低效率值对应横盘震荡（max_hold_timeout高发区），高效率对应趋势行情。值域[-1,1]，正值表示趋势高效，负值表示震荡低效。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_me",
            name="Market Efficiency Coefficient",
            display_name="市场效率系数",
            description="通过比较对数收益率净变化与每日绝对对数收益率之和的比值，度量价格运动的效率。低效率值对应横盘震荡（max_hold_timeout高发区），高效率对应趋势行情。值域[-1,1]，正值表示趋势高效，负值表示震荡低效。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        log_ret = np.log(close / close.shift(1))
        window = 20
        net_change = np.log(close / close.shift(window))
        path = log_ret.abs().rolling(window=window).sum()
        efficiency = net_change / path
        # Normalize to [-1, 1] using rolling rank over 100 periods
        rank = efficiency.rolling(window=100).rank(pct=True) - 0.5
        result = 2 * rank  # range roughly [-1, 1]
        result = result.clip(-1, 1)
        return result.fillna(0)
