"""AI因子: 均值回复时间衰减因子 | 置信:65% | 基于价格偏离均线的时间长度和幅度，判断当前是否处于均值回复的高风险窗口。长时间偏离且未回调时，后续发生回撤或假突破的概率增大（类似max_hold_timeout亏损）。因子值越低，代表价格偏离均值越久，越容易触发反向波动；因子值越高，代表价格刚回归或偏离很短期，趋势延续概率大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Time_Decay(BaseFactor):
    """基于价格偏离均线的时间长度和幅度，判断当前是否处于均值回复的高风险窗口。长时间偏离且未回调时，后续发生回撤或假突破的概率增大（类似max_hold_timeout亏损）。因子值越低，代表价格偏离均值越久，越容易触发反向波动；因子值越高，代表价格刚回归或偏离很短期，趋势延续概率大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_meanrevertime",
            name="Mean Reversion Time Decay",
            display_name="均值回复时间衰减因子",
            description="基于价格偏离均线的时间长度和幅度，判断当前是否处于均值回复的高风险窗口。长时间偏离且未回调时，后续发生回撤或假突破的概率增大（类似max_hold_timeout亏损）。因子值越低，代表价格偏离均值越久，越容易触发反向波动；因子值越高，代表价格刚回归或偏离很短期，趋势延续概率大。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        ma20 = close.rolling(20).mean()
        # 标准化偏离度（Z-score）
        std20 = close.rolling(20).std()
        z_score = (close - ma20) / (std20 + 1e-10)
        # 记录连续偏离方向（正或负）的天数
        direction = np.sign(z_score)
        consecutive = direction * (direction.shift(1) == direction).astype(int) * 1
        # 累积连续天数（保留符号）
        consec_days = direction.copy()
        for i in range(1, len(data)):
            if direction.iloc[i] == direction.iloc[i-1]:
                consec_days.iloc[i] = consec_days.iloc[i-1] + direction.iloc[i]
            else:
                consec_days.iloc[i] = direction.iloc[i]
        # 当前偏离幅度绝对值
        abs_z = z_score.abs()
        # 时间衰减因子：连续偏离天数越长、幅度越大，因子越负
        raw = -consec_days.abs() * abs_z
        # 归一化到[-1,1]，使用滚动窗口
        roll = raw.rolling(60).quantile(0.95)
        roll_min = raw.rolling(60).quantile(0.05)
        result = np.where(roll - roll_min > 0, 
                          2 * (raw - roll_min) / (roll - roll_min) - 1, 
                          0)
        return pd.Series(result, index=data.index).fillna(0)
