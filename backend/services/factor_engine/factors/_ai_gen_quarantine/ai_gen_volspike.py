"""AI因子: 波动率异常膨胀 | 置信:65% | 当价格波动率在近期急剧上升且方向不明时，容易触发止损或平仓亏损。因子通过计算当前波动率与历史波动率的Z-score，并结合价格方向的不确定性（如价格在布林带内反复穿越中轨），输出[-1,+1]，正值表示高风险波动膨胀状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySpike(BaseFactor):
    """当价格波动率在近期急剧上升且方向不明时，容易触发止损或平仓亏损。因子通过计算当前波动率与历史波动率的Z-score，并结合价格方向的不确定性（如价格在布林带内反复穿越中轨），输出[-1,+1]，正值表示高风险波动膨胀状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volspike",
            name="volatility_spike",
            display_name="波动率异常膨胀",
            description="当价格波动率在近期急剧上升且方向不明时，容易触发止损或平仓亏损。因子通过计算当前波动率与历史波动率的Z-score，并结合价格方向的不确定性（如价格在布林带内反复穿越中轨），输出[-1,+1]，正值表示高风险波动膨胀状态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算日内收益率
        returns = np.log(data['close'] / data['close'].shift(1))
        # 计算20日滚动波动率
        vol_20 = returns.rolling(20).std()
        # 计算60日滚动波动率的均值和标准差
        vol_60_mean = vol_20.rolling(60).mean()
        vol_60_std = vol_20.rolling(60).std()
        # Z-score
        z_score = (vol_20 - vol_60_mean) / vol_60_std
        # 处理缺失值
        z_score = z_score.fillna(0)
        # 价格方向不确定性：价格在20日布林带内穿越中轨的频率
        mid = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        upper = mid + 2*std
        lower = mid - 2*std
        # 计算价格在上下轨之间穿越中轨的次数（过去5根K线）
        cross_mid = ((data['close'] > mid) & (data['close'].shift(1) <= mid)) | ((data['close'] < mid) & (data['close'].shift(1) >= mid))
        cross_count = cross_mid.rolling(5).sum()
        # 归一化到[0,1]
        cross_norm = np.clip(cross_count / 5, 0, 1)
        # 组合因子：高波动Z-score + 高频率穿越 -> 正值
        factor = np.clip(z_score * 0.7 + cross_norm * 0.3, -1, 1)
        return factor
