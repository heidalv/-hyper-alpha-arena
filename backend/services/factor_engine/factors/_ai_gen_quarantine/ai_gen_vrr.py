"""AI因子: 成交量反转模式 | 置信:60% | 基于成交量异常与价格反转的复合因子。当价格在短期内出现明显方向性移动（如连续上涨/下跌）且成交量急剧放大后快速萎缩，预示趋势可能衰竭，产生反向信号。正值表示看多，负值表示看空。针对做空亏损模式，该因子会在成交量异常放大后的反转点发出正信号，帮助避免做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeReversalRegime(BaseFactor):
    """基于成交量异常与价格反转的复合因子。当价格在短期内出现明显方向性移动（如连续上涨/下跌）且成交量急剧放大后快速萎缩，预示趋势可能衰竭，产生反向信号。正值表示看多，负值表示看空。针对做空亏损模式，该因子会在成交量异常放大后的反转点发出正信号，帮助避免做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vrr",
            name="Volume Reversal Regime",
            display_name="成交量反转模式",
            description="基于成交量异常与价格反转的复合因子。当价格在短期内出现明显方向性移动（如连续上涨/下跌）且成交量急剧放大后快速萎缩，预示趋势可能衰竭，产生反向信号。正值表示看多，负值表示看空。针对做空亏损模式，该因子会在成交量异常放大后的反转点发出正信号，帮助避免做空。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        short_window = 5
        long_window = 20
        vol_ratio_thresh = 2.0
    
        close = data['close']
        volume = data['volume']
    
        # 短期价格变化率
        price_change = close.pct_change(short_window)
        # 成交量均线
        vol_ma = volume.rolling(long_window).mean()
        vol_ratio = volume / vol_ma
    
        # 检测价格快速上涨后成交量急剧放大（潜在的顶部反转）
        up_surge = (price_change > 0.03) & (vol_ratio > vol_ratio_thresh)
        # 快速下跌后成交量放大（潜在的底部反转）
        down_surge = (price_change < -0.03) & (vol_ratio > vol_ratio_thresh)
    
        # 反转信号：在放量之后，如果下一根K线价格反向移动（或者成交量回落）
        # 这里使用未来1期验证，但因子不能有未来信息，因此我们用当前成交量萎缩作为信号
        # 实际中，我们可以在放量后的第二根K线检测成交量回落
        vol_ratio_lag = vol_ratio.shift(1)
        # 放量后一根成交量回落
        vol_revert = (vol_ratio < vol_ratio_lag * 0.8) & (vol_ratio_lag > vol_ratio_thresh)
    
        # 结合价格方向：如果是前期上涨后的放量回落，则认为有做空风险（因子为正，看多）
        # 前期上涨：用短期价格变化判断
        up_before = close.pct_change(short_window).shift(1) > 0.02
        down_before = close.pct_change(short_window).shift(1) < -0.02
    
        # 做空风险信号（因子为正）：前期上涨，然后放量回落 -> 可能反转下行，此时做空风险高，因子应看多（正）
        sell_risk = up_before & vol_revert
        # 做多风险信号（因子为负）：前期下跌，然后放量回落 -> 可能反转上行，此时做多风险高，因子应看空（负）
        buy_risk = down_before & vol_revert
    
        signal = pd.Series(0.0, index=data.index)
        signal[sell_risk] = 1.0
        signal[buy_risk] = -1.0
        return signal
