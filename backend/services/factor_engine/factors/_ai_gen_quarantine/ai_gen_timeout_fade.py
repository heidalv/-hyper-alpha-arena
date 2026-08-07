"""AI因子: 持仓超时衰竭因子 | 置信:55% | 模拟趋势持续一定时间后动能衰减，价格容易反转。基于连续同向的K线数量（上涨或下跌）以及累计收益率，当连续同向K线超过阈值且累计收益率过大时，发出反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Time_Out_Fade(BaseFactor):
    """模拟趋势持续一定时间后动能衰减，价格容易反转。基于连续同向的K线数量（上涨或下跌）以及累计收益率，当连续同向K线超过阈值且累计收益率过大时，发出反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_fade",
            name="Time_Out_Fade",
            display_name="持仓超时衰竭因子",
            description="模拟趋势持续一定时间后动能衰减，价格容易反转。基于连续同向的K线数量（上涨或下跌）以及累计收益率，当连续同向K线超过阈值且累计收益率过大时，发出反向信号。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            import pandas as pd
            # 参数
            max_consecutive = 5
            return_threshold = 0.03
            # 计算连续同向K线个数（基于收盘价涨跌）
            direction = (data['close'].diff() > 0).astype(int)  # 1涨0跌
            # 自定义函数计算连续相同值
            consecutive = direction.groupby((direction != direction.shift()).cumsum()).cumcount() + 1
            # 对下跌方向取负值表示连续下跌
            consecutive_signed = consecutive * (2*direction - 1)  # 涨为正，跌为负
            # 计算累计收益率（从连续开始到现在）
            # 用简单方法：计算从上一个反向点开始的累计收益
            group_id = (direction != direction.shift()).cumsum()
            cum_ret = data['close'].pct_change().groupby(group_id).cumsum()
            # 条件：连续同向超过阈值且累计收益超过阈值
            long_cond = (consecutive_signed >= max_consecutive) & (cum_ret > return_threshold)
            short_cond = (consecutive_signed <= -max_consecutive) & (cum_ret < -return_threshold)
            # 信号：连续上涨后看跌，连续下跌后看涨
            signal = pd.Series(0.0, index=data.index)
            signal[long_cond] = -1.0
            signal[short_cond] = 1.0
            return signal
