"""AI因子: 空头陷阱检测因子 | 置信:60% | 检测价格突破近期新高后迅速回落，判定为空头陷阱概率。当因子为负值时，表示当前环境容易触发空头止损，建议避免做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTrapDetector(BaseFactor):
    """检测价格突破近期新高后迅速回落，判定为空头陷阱概率。当因子为负值时，表示当前环境容易触发空头止损，建议避免做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_trap",
            name="Short_Trap_Detector",
            display_name="空头陷阱检测因子",
            description="检测价格突破近期新高后迅速回落，判定为空头陷阱概率。当因子为负值时，表示当前环境容易触发空头止损，建议避免做空。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算20日最高价
        data['high_20'] = data['high'].rolling(20).max()
        # 突破比率：今日最高相对于20日最高
        data['break_ratio'] = data['high'] / data['high_20'] - 1
        # 日内跌幅：从开盘到收盘的收益率
        data['intraday_ret'] = (data['close'] - data['open']) / data['open']
        # 判断是否假突破：突破且日内收跌
        data['trap_signal'] = np.where((data['break_ratio'] > 0.005) & (data['intraday_ret'] < -0.01), -1, 0)
        # 结合成交量确认，成交量放大则加强
        vol_ma = data['volume'].rolling(10).mean()
        data['vol_ratio'] = data['volume'] / vol_ma
        # 最终因子：trap信号乘以成交量权重，再clip到[-1,1]
        data['factor'] = data['trap_signal'] * np.clip(data['vol_ratio'] / 3, 0, 1)
        return data['factor'].clip(-1, 1)
