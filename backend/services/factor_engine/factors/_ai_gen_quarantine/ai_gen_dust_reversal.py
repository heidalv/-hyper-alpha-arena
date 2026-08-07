"""AI因子: 尘埃清理与主力反转复合因子 | 置信:50% | 结合微小订单清理（dust cleanup）和主力平仓反转（master running close）两种亏损模式。当出现极低成交量小幅价格波动（尘埃清理）后，若随后成交量萎缩或价格加速度下降，则预示行情反转。同时检测价格突破前期高低点后成交量快速衰减（主力离场信号）。输出为正表示看多反转，负表示看空反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupMasterRunReversalComposite(BaseFactor):
    """结合微小订单清理（dust cleanup）和主力平仓反转（master running close）两种亏损模式。当出现极低成交量小幅价格波动（尘埃清理）后，若随后成交量萎缩或价格加速度下降，则预示行情反转。同时检测价格突破前期高低点后成交量快速衰减（主力离场信号）。输出为正表示看多反转，负表示看空反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_reversal",
            name="Dust Cleanup & Master Run Reversal Composite",
            display_name="尘埃清理与主力反转复合因子",
            description="结合微小订单清理（dust cleanup）和主力平仓反转（master running close）两种亏损模式。当出现极低成交量小幅价格波动（尘埃清理）后，若随后成交量萎缩或价格加速度下降，则预示行情反转。同时检测价格突破前期高低点后成交量快速衰减（主力离场信号）。输出为正表示看多反转，负表示看空反转。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
    
        df = data.copy()
        open_ = df['open']
        high = df['high']
        low = df['low']
        close = df['close']
        volume = df['volume']
    
        # 参数
        window = 10
    
        # 计算价格变化幅度（range）和成交量
        range_ = high - low
        range_pct = range_ / close.shift(1) * 100  # 百分比范围
    
        # 尘埃清理条件：小幅度（小于0.5%）且成交量极度萎缩（小于前5日均值的30%）
        vol_ma5 = volume.rolling(5).mean()
        dust_condition = (range_pct < 0.5) & (volume < vol_ma5 * 0.3)
    
        # 主力平仓反转：价格创近期新高/新低时成交量却减小（背离）
        # 计算价格方向：收盘价相对前N日最高最低
        rolling_high = high.rolling(window).max()
        rolling_low = low.rolling(window).min()
    
        # 价格突破前高/前低且成交量未同步放大
        # 使用最高价突破前高作为信号
        new_high = high >= rolling_high.shift(1)  # 当前最高价超过之前window期内最高
        new_low = low <= rolling_low.shift(1)
    
        # 成交量变化：当前volume相对前几日均值
        vol_change = volume / vol_ma5 - 1
    
        # 主力离场：价格创新高但成交量萎缩（负vol_change）或者创新低且成交量萎缩
        master_sell = new_high & (vol_change < -0.3)  # 成交量萎缩30%以上，可能主力拉高出货后平仓
        master_buy = new_low & (vol_change < -0.3)   # 向下突破但无成交量，空头衰竭
    
        # 合成信号：尘埃清理后需要观察后续价格方向，我们使用未来一期价格变化（需避免未来信息？这里用滞后一期检测）
        # 实际上因子只能用当前信息，所以我们用当前条件组合，不引用未来
        # 结合尘埃和主力信号：若dust_condition发生，且随后出现master信号，则反转概率大。但需要shift
        # 由于因子只能使用历史，我们将dust_condition标记，然后在下一期使用master信号
        # 这里简化：用当前是否同时满足dust_condition和master信号（或近期内）
        # 定义近期：前5天内出现过dust_condition
        dust_history = dust_condition.rolling(5).sum() > 0
    
        # 综合信号：出现dust_history并且当前有master信号，或者当前同时满足（即小幅度低量后出现背离）
        composite_sell = master_sell & dust_history
        composite_buy = master_buy & dust_history
    
        # 生成连续值：强度使用vol_change的绝对值加权
        raw = (composite_buy.astype(int) * -vol_change) - (composite_sell.astype(int) * -vol_change)
        # 归一化到[-1,1]
        result = np.clip(raw, -1, 1)
    
        return pd.Series(result, index=df.index).fillna(0)
