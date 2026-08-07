"""AI因子: 反转强度确认 | 置信:60% | 结合价格与移动平均线的偏离程度和成交量变化来量化反转信号强度。当价格偏离均线超过2个标准差且成交量骤增时，认为反转概率高。正值表示向上反转，负值表示向下反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalStrengthWithVolumeConfirmation(BaseFactor):
    """结合价格与移动平均线的偏离程度和成交量变化来量化反转信号强度。当价格偏离均线超过2个标准差且成交量骤增时，认为反转概率高。正值表示向上反转，负值表示向下反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_confirm",
            name="Reversal Strength with Volume Confirmation",
            display_name="反转强度确认",
            description="结合价格与移动平均线的偏离程度和成交量变化来量化反转信号强度。当价格偏离均线超过2个标准差且成交量骤增时，认为反转概率高。正值表示向上反转，负值表示向下反转。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import pandas as pd
        import numpy as np
    
        close = data['close']
        volume = data['volume']
    
        # 计算20日均线和标准差
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
    
        # 价格偏离均线的Z-score
        z_score = (close - ma) / std
    
        # 成交量变化率（相对前一周期）
        vol_change = volume.pct_change()
        # 成交量突增阈值
        vol_surge = vol_change > 2.0
    
        # 极端偏离条件：|z_score| > 2
        extreme_overbought = z_score > 2.0
        extreme_oversold = z_score < -2.0
    
        # 信号：超买区域且成交量突增，预示向下反转；超卖区域且成交量突增，预示向上反转
        signal = pd.Series(0.0, index=data.index)
        signal[extreme_overbought & vol_surge] = -1.0
        signal[extreme_oversold & vol_surge] = 1.0
    
        # 加入价格动量过滤：如果当前价格已经反向移动超过0.5%，增加置信度
        ret = close.pct_change()
        reverse_up = (ret > 0.005) & extreme_oversold & vol_surge
        reverse_down = (ret < -0.005) & extreme_overbought & vol_surge
        signal[reverse_up] = 1.0
        signal[reverse_down] = -1.0
    
        return signal
