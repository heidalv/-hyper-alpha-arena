"""AI因子: 持仓时间风险指数 | 置信:60% | 计算价格相对于过去N周期（例如20）移动平均线的偏离程度，并结合当前价格与开仓后可能的最优/最差价格差距。模拟策略：当价格长时间（如20周期）未突破初始价格一定阈值时，后期超时平仓易亏损。因子为价格相对于MA的标准化偏差，正值表示价格高于均线有正向动量，负值表示低于均线有下跌风险，绝对值越大则超时风险越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holding_Time_Risk_Index(BaseFactor):
    """计算价格相对于过去N周期（例如20）移动平均线的偏离程度，并结合当前价格与开仓后可能的最优/最差价格差距。模拟策略：当价格长时间（如20周期）未突破初始价格一定阈值时，后期超时平仓易亏损。因子为价格相对于MA的标准化偏差，正值表示价格高于均线有正向动量，负值表示低于均线有下跌风险，绝对值越大则超时风险越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtime",
            name="Holding Time Risk Index",
            display_name="持仓时间风险指数",
            description="计算价格相对于过去N周期（例如20）移动平均线的偏离程度，并结合当前价格与开仓后可能的最优/最差价格差距。模拟策略：当价格长时间（如20周期）未突破初始价格一定阈值时，后期超时平仓易亏损。因子为价格相对于MA的标准化偏差，正值表示价格高于均线有正向动量，负值表示低于均线有下跌风险，绝对值越大则超时风险越高。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20).mean()
        # 标准化偏差： (close - ma) / 近期波动率
        vol = close.pct_change().rolling(20).std() * close  # 价格波动幅度
        deviation = (close - ma) / (vol + 1e-10)
        # 使用tanh限制在[-1,1]
        result = np.tanh(deviation)
        return result
