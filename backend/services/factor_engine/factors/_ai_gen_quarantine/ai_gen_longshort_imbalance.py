"""AI因子: 多空失衡指标 | 置信:55% | 许多亏损发生在多空操作均被止损/平仓时，暗示多空力量失衡导致方向持续不利。通过模拟多空压力：使用价格上涨期间成交量的累积（买入压力）与价格下跌期间成交量的累积（卖出压力）的比率，再经归一化处理。当比率极端偏离时，预示趋势可能反转或假突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LongShortImbalance(BaseFactor):
    """许多亏损发生在多空操作均被止损/平仓时，暗示多空力量失衡导致方向持续不利。通过模拟多空压力：使用价格上涨期间成交量的累积（买入压力）与价格下跌期间成交量的累积（卖出压力）的比率，再经归一化处理。当比率极端偏离时，预示趋势可能反转或假突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_longshort_imbalance",
            name="long_short_imbalance",
            display_name="多空失衡指标",
            description="许多亏损发生在多空操作均被止损/平仓时，暗示多空力量失衡导致方向持续不利。通过模拟多空压力：使用价格上涨期间成交量的累积（买入压力）与价格下跌期间成交量的累积（卖出压力）的比率，再经归一化处理。当比率极端偏离时，预示趋势可能反转或假突破。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算日内价格变化
        price_change = data['close'] - data['close'].shift(1)
        # 上涨时成交量视为买方压力，下跌时视为卖方压力
        buy_vol = np.where(price_change > 0, data['volume'], 0)
        sell_vol = np.where(price_change < 0, data['volume'], 0)
        # 滚动窗口累积（例如20期）
        window = 20
        cum_buy = pd.Series(buy_vol).rolling(window).sum()
        cum_sell = pd.Series(sell_vol).rolling(window).sum()
        # 避免除零
        ratio = cum_buy / (cum_buy + cum_sell + 1e-10)
        # 比率偏离0.5的程度，并映射到[-1,1]
        # 当ratio靠近0或1时接近-1（极端失衡预示反转风险），靠近0.5时接近0
        deviation = np.abs(ratio - 0.5) * 2  # 变成[0,1]
        # 使用符号：若ratio>0.6（买方过度）则负值，若ratio<0.4（卖方过度）则正值？
        # 这里统一：极端失衡无论方向都视为风险信号，故均匀映射到负值
        factor = -deviation  # 范围[-1,0]
        # 增加一点方向性：若ratio>0.6则更负，若ratio<0.4也更负，中间为0
        factor = np.where(deviation > 0.2, -deviation, 0)
        factor = np.clip(factor, -1, 0)
        return pd.Series(factor, index=data.index)
