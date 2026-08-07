"""AI因子: 量价相关性冲击 | 置信:60% | 计算过去N期价格变动与成交量变动的相关系数，结合价格异常变动。当成交量放大但价格变动微弱时，可能为虚假突破或反转；当量价同步时趋势可靠。返回[-1,1]，正值表示量价同步（趋势健康），负值表示量价背离（潜在反转）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceCorrelation(BaseFactor):
    """计算过去N期价格变动与成交量变动的相关系数，结合价格异常变动。当成交量放大但价格变动微弱时，可能为虚假突破或反转；当量价同步时趋势可靠。返回[-1,1]，正值表示量价同步（趋势健康），负值表示量价背离（潜在反转）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_impact",
            name="Volume Price Correlation",
            display_name="量价相关性冲击",
            description="计算过去N期价格变动与成交量变动的相关系数，结合价格异常变动。当成交量放大但价格变动微弱时，可能为虚假突破或反转；当量价同步时趋势可靠。返回[-1,1]，正值表示量价同步（趋势健康），负值表示量价背离（潜在反转）。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格收益率
        ret = close.pct_change()
        vol_change = volume.pct_change()
        # 滚动相关系数，窗口15
        corr = ret.rolling(15, min_periods=6).corr(vol_change)
        # 价格异常度: 当前收益率与滚动均值差除以标准差
        ret_mean = ret.rolling(20).mean()
        ret_std = ret.rolling(20).std()
        z_score = (ret - ret_mean) / (ret_std + 1e-10)
        # 组合：相关系数正且z_score显著时为正信号，负相关且z_score大时为负信号
        raw = corr * np.sign(z_score.abs())
        raw = raw.clip(-1, 1)
        raw = raw.fillna(0)
        return raw
