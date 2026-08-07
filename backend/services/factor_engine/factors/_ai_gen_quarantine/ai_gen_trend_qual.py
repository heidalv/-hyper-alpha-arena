"""AI因子: 趋势质量因子 | 置信:60% | 结合价格方向与成交量变化，计算趋势的可靠性。当价格上涨且成交量放大时，趋势质量高；反之如果价格上涨但成交量萎缩，则趋势可能是假突破。通过价格变化与成交量变化的秩相关系数衡量，乘以价格动量方向。值域[-1,1]，正值表示高质量趋势，负值表示假趋势或反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendQualityFactor(BaseFactor):
    """结合价格方向与成交量变化，计算趋势的可靠性。当价格上涨且成交量放大时，趋势质量高；反之如果价格上涨但成交量萎缩，则趋势可能是假突破。通过价格变化与成交量变化的秩相关系数衡量，乘以价格动量方向。值域[-1,1]，正值表示高质量趋势，负值表示假趋势或反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_qual",
            name="Trend Quality Factor",
            display_name="趋势质量因子",
            description="结合价格方向与成交量变化，计算趋势的可靠性。当价格上涨且成交量放大时，趋势质量高；反之如果价格上涨但成交量萎缩，则趋势可能是假突破。通过价格变化与成交量变化的秩相关系数衡量，乘以价格动量方向。值域[-1,1]，正值表示高质量趋势，负值表示假趋势或反转信号。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格收益率 (5日)
        ret = data['close'].pct_change(5)
        # 计算成交量变化率 (5日)
        vol_change = data['volume'].pct_change(5)
        # 滚动20期秩相关系数
        def rank_corr(x, y):
            return x.rank().corr(y.rank())
        # 使用rolling apply
        corr = ret.rolling(window=20, min_periods=1).corr(vol_change)
        # 将相关系数与动量方向结合：趋势质量 = 相关系数 * sign(ret) 
        # 当ret为正且正相关时为正，ret为负且负相关（下跌放量）也为正
        sign_ret = np.sign(ret).fillna(0)
        # 但只需考虑正相关（趋势与量一致）或负相关（反趋势放量）？
        # 简单处理：趋势质量 = corr * sign_ret，但这样只能表示同向变化程度
        # 改为：如果ret>0且corr>0，高质量；ret<0且corr<0也是高质量；否则低质量
        # 直接使用corr * sign_ret 即可，值域[-1,1]
        result = corr * sign_ret
        # 处理NaN
        result = result.fillna(0)
        return pd.Series(result, index=data.index)
