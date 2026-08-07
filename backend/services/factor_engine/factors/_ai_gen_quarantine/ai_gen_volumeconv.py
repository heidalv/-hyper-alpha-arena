"""AI因子: 量价背离因子 | 置信:60% | 比较价格变化与成交量变化的方向，当价格上涨但成交量萎缩（背离），或价格下跌但成交量放大（恐慌），输出负向信号。计算过去N周期价格收益率与成交量变化率的相关系数或符号一致性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence(BaseFactor):
    """比较价格变化与成交量变化的方向，当价格上涨但成交量萎缩（背离），或价格下跌但成交量放大（恐慌），输出负向信号。计算过去N周期价格收益率与成交量变化率的相关系数或符号一致性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumeconv",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="比较价格变化与成交量变化的方向，当价格上涨但成交量萎缩（背离），或价格下跌但成交量放大（恐慌），输出负向信号。计算过去N周期价格收益率与成交量变化率的相关系数或符号一致性。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格收益率（5天）
        price_ret = close.pct_change(5)
        # 成交量变化率（5天）
        volume_change = volume.pct_change(5)
        # 剔除NaN
        # 计算信号：价格涨而量缩（price_ret>0, volume_change<0）=> 背离=负向
        # 价格跌而量增（price_ret<0, volume_change>0）=> 恐慌=负向
        # 价格涨而量增（正向健康）=> 正向
        # 价格跌而量缩（缩量下跌，可能底部）=> 正向？谨慎，先给中性
        # 使用符号乘积
        sign_price = np.sign(price_ret)
        sign_vol = np.sign(volume_change)
        # 乘积：1表示同向，-1表示反向
        product = sign_price * sign_vol
        # 当同向时，如果价格涨，product=1 -> 正向信号；如果价格跌，product=1 -> 也是正向？但下跌放量是坏的，需要区分
        # 细化：价格涨且量增 => 好；价格跌且量减 => 可能好转；价格涨且量缩 => 坏；价格跌且量增 => 坏
        # 用price_ret的正负来调整
        bullish = price_ret > 0
        bearish = price_ret < 0
        vol_up = volume_change > 0
        vol_down = volume_change < 0
        # 正向场景：涨且量增或跌且量缩
        pos = (bullish & vol_up) | (bearish & vol_down)
        # 负向场景：涨且量缩或跌且量增
        neg = (bullish & vol_down) | (bearish & vol_up)
        result = np.where(neg, -1.0, 0.0)
        result = np.where(pos, 1.0, result)
        result = pd.Series(result, index=data.index)
        # 前5期填充0
        result.iloc[:5] = 0.0
        return result
