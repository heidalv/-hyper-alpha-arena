"""AI因子: 成交量确认因子 | 置信:62% | 衡量价格变动与成交量变动的方向一致性。当价格上涨时成交量同步放大（或价格下跌时成交量同步萎缩），表明趋势有成交量支持，因子值接近+1；当价格与成交量方向相反（如放量下跌或缩量上涨），表明趋势可能不稳固，因子值接近-1。有助于过滤因缺乏成交量确认而导致的假突破亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmationCoefficient(BaseFactor):
    """衡量价格变动与成交量变动的方向一致性。当价格上涨时成交量同步放大（或价格下跌时成交量同步萎缩），表明趋势有成交量支持，因子值接近+1；当价格与成交量方向相反（如放量下跌或缩量上涨），表明趋势可能不稳固，因子值接近-1。有助于过滤因缺乏成交量确认而导致的假突破亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcc",
            name="Volume Confirmation Coefficient",
            display_name="成交量确认因子",
            description="衡量价格变动与成交量变动的方向一致性。当价格上涨时成交量同步放大（或价格下跌时成交量同步萎缩），表明趋势有成交量支持，因子值接近+1；当价格与成交量方向相反（如放量下跌或缩量上涨），表明趋势可能不稳固，因子值接近-1。有助于过滤因缺乏成交量确认而导致的假突破亏损。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格变化率
        price_ret = data['close'].pct_change()
        # 计算成交量变化率
        volume_ret = data['volume'].pct_change()
        # 过去N天滚动计算价格与成交量变化率的相关性，但短周期更敏感
        window = 10
        # 标准化后相乘再滚动平均作为方向一致性的度量
        # 简单方法：乘积的符号和大小
        product = price_ret * volume_ret
        # 滚动平均乘积，正数表示正向确认，负数表示背离
        avg_product = product.rolling(window).mean()
        # 使用tanh压缩到[-1,1] 加一个缩放因子
        result = np.tanh(avg_product * 10)  # 适当放大敏感性
        result = result.fillna(0.0)
        return result
