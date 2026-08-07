"""AI因子: 短期动量衰竭 | 置信:60% | 捕捉当短期价格快速运动但未能突破关键阻力/支撑位，随后快速反转的形态。这种模式与'master_running_close_tiny'的快速平仓亏损高度相关。计算最近3分钟收益率与最近8分钟收益率的差值，并结合价格相对布林带上下轨的位置，当动量衰竭时给出反方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Short-term Momentum Failure(BaseFactor):
    """捕捉当短期价格快速运动但未能突破关键阻力/支撑位，随后快速反转的形态。这种模式与'master_running_close_tiny'的快速平仓亏损高度相关。计算最近3分钟收益率与最近8分钟收益率的差值，并结合价格相对布林带上下轨的位置，当动量衰竭时给出反方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momfail",
            name="Short-term Momentum Failure",
            display_name="短期动量衰竭",
            description="捕捉当短期价格快速运动但未能突破关键阻力/支撑位，随后快速反转的形态。这种模式与'master_running_close_tiny'的快速平仓亏损高度相关。计算最近3分钟收益率与最近8分钟收益率的差值，并结合价格相对布林带上下轨的位置，当动量衰竭时给出反方向信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            close = data['close']
            high = data['high']
            low = data['low']
    
            # 短期收益率
            ret_3 = close.pct_change(3)
            ret_8 = close.pct_change(8)
            mom_diff = (ret_3 - ret_8).fillna(0)
    
            # 布林带上下轨 (20周期)
            ma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper = ma20 + 2 * std20
            lower = ma20 - 2 * std20
    
            # 价格相对位置
            pos = (close - lower) / (upper - lower).replace(0, 1e-10)
            # 当动量衰竭：若价格在上轨附近且正向动量衰竭 -> 看空；在下轨附近且负向动量衰竭 -> 看多
            signal = -mom_diff * (pos - 0.5) * 4  # 乘积放大
            result = np.clip(signal, -1, 1)
            return result.fillna(0.0)
