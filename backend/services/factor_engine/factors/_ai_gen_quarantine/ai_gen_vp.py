"""AI因子: 量价相关性 | 置信:55% | 计算滚动窗口内价格变动与成交量变化的相关性。当相关性接近0时，量价关系不明确，市场regime未知。使用20周期皮尔逊相关系数，并取相反数使相关性低时因子接近-1，高相关性时接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class volume_price_correlation(BaseFactor):
    """计算滚动窗口内价格变动与成交量变化的相关性。当相关性接近0时，量价关系不明确，市场regime未知。使用20周期皮尔逊相关系数，并取相反数使相关性低时因子接近-1，高相关性时接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vp",
            name="volume_price_correlation",
            display_name="量价相关性",
            description="计算滚动窗口内价格变动与成交量变化的相关性。当相关性接近0时，量价关系不明确，市场regime未知。使用20周期皮尔逊相关系数，并取相反数使相关性低时因子接近-1，高相关性时接近+1。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格收益率和成交量变化率
        price_ret = data['close'].pct_change()
        volume_chg = data['volume'].pct_change()
        # 滚动20周期相关系数
        window = 20
        corr = price_ret.rolling(window).corr(volume_chg)
        # 当相关系数绝对值小（接近0）时，regime未知；取负号使输出负值
        # 映射：将相关系数从[-1,1]映射到[-1,1]，但希望0附近为-1，极端±1为+1
        # 使用变换：sign(corr) * (1 - |corr|) 会使得0处为0，不对。更好：直接取负的绝对值？
        # 实际想要：corr越接近0，因子越接近-1；corr绝对值越接近1，因子越接近+1
        # 使用 1 - 2*abs(corr) 得到：abs(corr)=0时为1，abs(corr)=1时为-1，再取负得到-1到1
        result = 2 * np.abs(corr) - 1  # 当corr=0 => -1, corr=±1 => 1
        result = result.fillna(0)
        return result
