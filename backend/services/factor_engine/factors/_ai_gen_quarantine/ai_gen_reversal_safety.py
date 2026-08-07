"""AI因子: 反转安全度 | 置信:60% | 通过短期价格乖离率与成交量异常来识别潜在反转点。当价格短期偏离均线过大且成交量异常放大时，反转概率高，做多风险大。因子值高表示安全（无反转信号），值低表示反转风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversal_Safety(BaseFactor):
    """通过短期价格乖离率与成交量异常来识别潜在反转点。当价格短期偏离均线过大且成交量异常放大时，反转概率高，做多风险大。因子值高表示安全（无反转信号），值低表示反转风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_safety",
            name="Reversal Safety",
            display_name="反转安全度",
            description="通过短期价格乖离率与成交量异常来识别潜在反转点。当价格短期偏离均线过大且成交量异常放大时，反转概率高，做多风险大。因子值高表示安全（无反转信号），值低表示反转风险高。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        # 计算5日乖离率
        ma5 = close.rolling(5).mean()
        bias = (close - ma5) / ma5.replace(0, np.nan) * 100  # 百分比
        # 计算20日均量
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20.replace(0, np.nan)
        # 反转信号：乖离率绝对值大且成交量异常高
        bias_abs = np.abs(bias)
        # 将乖离率限幅到[0,20]并归一化
        bias_norm = np.clip(bias_abs / 20, 0, 1)
        vol_norm = np.clip((vol_ratio - 1) / 2, 0, 1)  # 成交量超出均值1倍以上视为异常
        # 反转风险 = 乖离率 * 成交量异常
        reversal_risk = bias_norm * vol_norm
        # 安全度 = 1 - reversal_risk，映射到[-1,1]
        safety = 1 - reversal_risk
        safety = safety * 2 - 1
        safety = safety.fillna(0).clip(-1, 1)
        return safety
