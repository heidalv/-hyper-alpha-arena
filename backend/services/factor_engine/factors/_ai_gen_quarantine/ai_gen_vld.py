"""AI因子: 波动流态异常因子 | 置信:60% | 识别市场波动率与成交量的异常组合，当波动率处于历史低位且成交量骤缩时，或波动率异常高但成交量低迷时，市场状态可能切换为未知（regime=unknown），此时趋势策略容易亏损。因子输出正值表示高不确定性（适合反转或对冲），负值表示正常状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Liquidity_Disruption(BaseFactor):
    """识别市场波动率与成交量的异常组合，当波动率处于历史低位且成交量骤缩时，或波动率异常高但成交量低迷时，市场状态可能切换为未知（regime=unknown），此时趋势策略容易亏损。因子输出正值表示高不确定性（适合反转或对冲），负值表示正常状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vld",
            name="Volatility_Liquidity_Disruption",
            display_name="波动流态异常因子",
            description="识别市场波动率与成交量的异常组合，当波动率处于历史低位且成交量骤缩时，或波动率异常高但成交量低迷时，市场状态可能切换为未知（regime=unknown），此时趋势策略容易亏损。因子输出正值表示高不确定性（适合反转或对冲），负值表示正常状态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # 计算历史波动率（20日标准差）
        returns = close.pct_change()
        hist_vol = returns.rolling(20).std()

        # 计算成交量相对历史均值
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma

        # 波动率百分位（60天）
        vol_percentile = hist_vol.rolling(60).rank(pct=True)

        # 异常条件：低波动（<20%百分位）且低成交量（<0.5倍均值）或高波动（>80%百分位）且低成交量
        condition1 = (vol_percentile < 0.2) & (vol_ratio < 0.5)
        condition2 = (vol_percentile > 0.8) & (vol_ratio < 0.3)

        signal = pd.Series(0.0, index=close.index)
        signal[condition1 | condition2] = 1.0
        # 正常状态输出-0.5（轻微负向校准）
        signal[~(condition1 | condition2)] = -0.5
        return signal
