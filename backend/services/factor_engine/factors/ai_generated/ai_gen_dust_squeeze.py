"""AI因子: 残渣清理挤压 | 置信:50% | 捕捉低流动性下微小订单被清除后价格小幅跳空的特征：结合成交量萎缩和价格窄幅震荡后的突然小幅度突破，预测类似dust_cleanup亏损模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupSqueeze(BaseFactor):
    """捕捉低流动性下微小订单被清除后价格小幅跳空的特征：结合成交量萎缩和价格窄幅震荡后的突然小幅度突破，预测类似dust_cleanup亏损模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_squeeze",
            name="Dust Cleanup Squeeze",
            display_name="残渣清理挤压",
            description="捕捉低流动性下微小订单被清除后价格小幅跳空的特征：结合成交量萎缩和价格窄幅震荡后的突然小幅度突破，预测类似dust_cleanup亏损模式。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 成交量萎缩: 当前成交量与20日均量之比
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        # 窄幅震荡: 过去5根K线的高低范围相对价格
        range5 = (data['high'].rolling(5).max() - data['low'].rolling(5).min()) / data['close'].rolling(5).mean()
        # 小幅度突破: 当前收盘价相对于过去5日均价的偏离
        ma5 = data['close'].rolling(5).mean()
        breakout = (data['close'] - ma5) / ma5
        # 条件: 成交量低(vol_ratio<0.7) 且 窄幅震荡(range5<0.02) 且 突破幅度小(breakout绝对值<0.01)
        cond_vol = (vol_ratio < 0.7).astype(int)
        cond_range = (range5 < 0.02).astype(int)
        cond_break = (breakout.abs() < 0.01).astype(int)
        # 综合得分
        raw = - (cond_vol * cond_range * cond_break) * (breakout * 100)  # 方向:向上的突破给负值，向下给正值？
        # 根据模式，dust_cleanup多是做多亏损，所以向上突破时信号为负
        result = raw.clip(-1, 1)
        return result.fillna(0)
