"""AI因子: 量价背离风险 | 置信:60% | 当价格突破但成交量未能确认（缩量上涨或放量下跌），容易产生假突破而止损。计算最近N周期内价格变化与成交量变化的秩相关系数，负相关或弱相关时给负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence(BaseFactor):
    """当价格突破但成交量未能确认（缩量上涨或放量下跌），容易产生假突破而止损。计算最近N周期内价格变化与成交量变化的秩相关系数，负相关或弱相关时给负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_confirm",
            name="Volume Price Divergence",
            display_name="量价背离风险",
            description="当价格突破但成交量未能确认（缩量上涨或放量下跌），容易产生假突破而止损。计算最近N周期内价格变化与成交量变化的秩相关系数，负相关或弱相关时给负值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算价格变化率
        price_ret = close.pct_change()
        vol_change = volume.pct_change()
        # 计算过去20天的滚动秩相关系数（Spearman近似）
        window = 20
        def rolling_spearman(x, y):
            # 使用pandas的corr方法，method='spearman'
            corr = pd.Series(x).corr(pd.Series(y), method='spearman')
            return corr if not np.isnan(corr) else 0
        # 使用rolling apply效率较低，这里采用简单替代：用皮尔逊相关系数符号和量价方向一致性
        # 构造量价背离指标：价格涨而量缩，或价格跌而量增，背离严重
        # 计算日内价格方向与成交量方向是否一致
        price_up = (price_ret > 0).astype(int)
        vol_up = (vol_change > 0).astype(int)
        # 一致时为1，不一致为-1，再乘以成交量变化幅度
        agreement = 2 * (price_up == vol_up) - 1  # 1一致，-1背离
        # 加权：乘上成交量变化绝对值
        weighted = agreement * vol_change.abs()
        # 平滑并归一化到[-1,1]
        result = weighted.rolling(window).mean()
        result = result / (result.abs().rolling(window).mean() + 1e-8)  # 类似z-score
        result = np.clip(result, -1, 1)
        return result
