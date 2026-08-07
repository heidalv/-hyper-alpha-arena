"""AI因子: 趋势效率比 | 置信:60% | 衡量价格运动的净方向性与总波动的比率。当市场呈现高效趋势时，该比率接近±1；在震荡无方向时接近0。该因子旨在避免在低效率震荡市做多，这类市场常导致持仓超时亏损。计算：(收盘-开盘)的滚动和 / (最高-最低)的滚动和，符号由净方向决定。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendEfficiencyRatio(BaseFactor):
    """衡量价格运动的净方向性与总波动的比率。当市场呈现高效趋势时，该比率接近±1；在震荡无方向时接近0。该因子旨在避免在低效率震荡市做多，这类市场常导致持仓超时亏损。计算：(收盘-开盘)的滚动和 / (最高-最低)的滚动和，符号由净方向决定。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ter",
            name="Trend Efficiency Ratio",
            display_name="趋势效率比",
            description="衡量价格运动的净方向性与总波动的比率。当市场呈现高效趋势时，该比率接近±1；在震荡无方向时接近0。该因子旨在避免在低效率震荡市做多，这类市场常导致持仓超时亏损。计算：(收盘-开盘)的滚动和 / (最高-最低)的滚动和，符号由净方向决定。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        window = 14
        o = data['open']
        h = data['high']
        l = data['low']
        c = data['close']
        net_move = (c - o).rolling(window).sum()
        total_path = (h - l).rolling(window).sum()
        er = np.abs(net_move) / total_path.replace(0, np.nan)
        result = np.sign(net_move) * er
        result = result.clip(-1, 1)
        return result
