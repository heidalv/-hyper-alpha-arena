"""AI因子: 反转强度 | 置信:60% | 基于日内价格极端值与收盘价的偏离程度，结合成交量放大，识别潜在反转点。当价格触及近期高低点但成交量异常时，预示反转风险。计算：先求日内波动幅度（最高-最低）/均价，再乘以收盘价相对于日内中点的偏离方向，并用标准化成交量加权。输出[-1,1]，正值表示收盘靠近高点且成交量高（可能见顶），负值相反。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalIntensity(BaseFactor):
    """基于日内价格极端值与收盘价的偏离程度，结合成交量放大，识别潜在反转点。当价格触及近期高低点但成交量异常时，预示反转风险。计算：先求日内波动幅度（最高-最低）/均价，再乘以收盘价相对于日内中点的偏离方向，并用标准化成交量加权。输出[-1,1]，正值表示收盘靠近高点且成交量高（可能见顶），负值相反。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_intensity",
            name="Reversal Intensity",
            display_name="反转强度",
            description="基于日内价格极端值与收盘价的偏离程度，结合成交量放大，识别潜在反转点。当价格触及近期高低点但成交量异常时，预示反转风险。计算：先求日内波动幅度（最高-最低）/均价，再乘以收盘价相对于日内中点的偏离方向，并用标准化成交量加权。输出[-1,1]，正值表示收盘靠近高点且成交量高（可能见顶），负值相反。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        open_ = data['open']
        volume = data['volume']
        # 日内平均价格
        avg_price = (high + low + close) / 3
        # 日内波动率
        range_ = high - low
        # 避免除以零
        range_pct = np.where(avg_price != 0, range_ / avg_price, 0)
        # 收盘相对于日内中点的位置：0.5为中间
        mid = (high + low) / 2
        close_position = (close - mid) / (range_ + 1e-10)  # -0.5到0.5
        # 成交量标准化（滚动窗口Z-score）
        vol_ma = volume.rolling(20).mean()
        vol_std = volume.rolling(20).std()
        vol_z = (volume - vol_ma) / (vol_std + 1e-10)
        # 组合：方向由close_position决定，强度由range_pct和vol_z共同作用
        raw = close_position * range_pct * (1 + np.tanh(vol_z))
        # 归一化到[-1,1] 使用tanh
        result = np.tanh(raw * 5)
        return pd.Series(result, index=data.index)
