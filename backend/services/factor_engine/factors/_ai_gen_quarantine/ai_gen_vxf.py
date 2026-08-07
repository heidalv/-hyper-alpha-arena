"""AI因子: 成交量极端流向 | 置信:60% | 检测成交量异常放大时价格方向的一致性。当成交量突然放大但价格窄幅震荡或方向频繁反转时，表明多空分歧严重，属于未知状态。因子返回[-1,1]，正向表示价格与成交量方向一致（趋势明确），负向表示分歧（未知状态）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeExtremeFlow(BaseFactor):
    """检测成交量异常放大时价格方向的一致性。当成交量突然放大但价格窄幅震荡或方向频繁反转时，表明多空分歧严重，属于未知状态。因子返回[-1,1]，正向表示价格与成交量方向一致（趋势明确），负向表示分歧（未知状态）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vxf",
            name="Volume Extreme Flow",
            display_name="成交量极端流向",
            description="检测成交量异常放大时价格方向的一致性。当成交量突然放大但价格窄幅震荡或方向频繁反转时，表明多空分歧严重，属于未知状态。因子返回[-1,1]，正向表示价格与成交量方向一致（趋势明确），负向表示分歧（未知状态）。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 计算20日成交量均值与标准差
        vol = data['volume']
        vol_ma = vol.rolling(window=20, min_periods=1).mean()
        vol_std = vol.rolling(window=20, min_periods=1).std()
        # 成交量异常指标：当前量超过均值+1倍标准差
        vol_ratio = (vol - vol_ma) / (vol_std + 1e-10)
        # 计算短期价格方向一致性（使用5日收盘价变化符号）
        ret = data['close'].pct_change(periods=1)
        sign_ret = np.sign(ret).rolling(window=5, min_periods=1).mean()  # -1到1
        # 构建方向一致性指标：若成交量异常且方向不明确（sign_ret接近0），则得分低
        # 在成交量正常时，用趋势强度替代
        trend_strength = sign_ret.abs()
        # 综合：当成交量异常时，强调方向一致性；否则使用趋势强度
        is_extreme = (vol_ratio > 1.0).astype(float)
        # 负向：当成交量极端且方向不一致时，输出负值
        score = (1 - is_extreme) * trend_strength + is_extreme * (2 * (sign_ret * 0.5 + 0.5) - 1)
        # 去除极端值并平滑
        result = score.rolling(window=3, min_periods=1).mean().fillna(0)
        return result.clip(-1, 1)
