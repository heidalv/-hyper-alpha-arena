"""AI因子: 反向陷阱 | 置信:60% | 检测价格在关键均线或前期高低点附近反复试探但未能有效突破的陷阱模式。当价格连续两次触及同一价位失败，且成交量递减时，表明反向资金埋伏。返回正值为看涨陷阱（看多），负值为看空陷阱（看空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ContrarianTrap(BaseFactor):
    """检测价格在关键均线或前期高低点附近反复试探但未能有效突破的陷阱模式。当价格连续两次触及同一价位失败，且成交量递减时，表明反向资金埋伏。返回正值为看涨陷阱（看多），负值为看空陷阱（看空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_contrarian_trap",
            name="Contrarian Trap",
            display_name="反向陷阱",
            description="检测价格在关键均线或前期高低点附近反复试探但未能有效突破的陷阱模式。当价格连续两次触及同一价位失败，且成交量递减时，表明反向资金埋伏。返回正值为看涨陷阱（看多），负值为看空陷阱（看空）。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 计算20期简单移动平均线
        sma20 = close.rolling(20).mean()
        # 价格相对于均线的偏离
        dev = (close - sma20) / sma20
        # 识别价格在均线上下反复：过去5根K线中至少3次接近均线（偏离<0.5%）
        near = (np.abs(dev) < 0.005).astype(int)
        near_count = near.rolling(5).sum()
        # 成交量萎缩：当前成交量低于过去20期均值
        vol_ma = volume.rolling(20).mean()
        vol_shrink = (volume < vol_ma * 0.8).astype(int)
        # 同时价格方向不明：最近3根K线收盘价变化小于0.3%
        pct_chg = close.pct_change()
        tiny_move = (np.abs(pct_chg) < 0.003).astype(int)
        tiny_count = tiny_move.rolling(3).sum()
        # 陷阱信号：接近均线频繁 + 成交量萎缩 + 窄幅震荡
        trap = (near_count >= 3) & (vol_shrink == 1) & (tiny_count >= 2)
        # 根据当前收盘价与均线的关系决定方向：若close<均线则为看涨陷阱（预期回归向上），反之为看空陷阱
        direction = np.where(close < sma20, 1, -1)
        result = trap.astype(float) * direction
        result = result.rolling(2).max().fillna(0)
        return result
