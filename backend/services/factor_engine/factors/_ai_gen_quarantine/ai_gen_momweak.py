"""AI因子: 动量弱势指标 | 置信:60% | 结合价格动量与成交量变化，识别上涨动能的衰减或假突破。当价格创出新高但成交量萎缩时，因子值为负（做多风险高）；当价格下跌时成交量放大，因子值也为负。该因子直接针对‘ai_reverse’和‘master_running_close_tiny’模式，帮助避免在弱势反弹中做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Weakness_Indicator(BaseFactor):
    """结合价格动量与成交量变化，识别上涨动能的衰减或假突破。当价格创出新高但成交量萎缩时，因子值为负（做多风险高）；当价格下跌时成交量放大，因子值也为负。该因子直接针对‘ai_reverse’和‘master_running_close_tiny’模式，帮助避免在弱势反弹中做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momweak",
            name="Momentum Weakness Indicator",
            display_name="动量弱势指标",
            description="结合价格动量与成交量变化，识别上涨动能的衰减或假突破。当价格创出新高但成交量萎缩时，因子值为负（做多风险高）；当价格下跌时成交量放大，因子值也为负。该因子直接针对‘ai_reverse’和‘master_running_close_tiny’模式，帮助避免在弱势反弹中做多。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算动量：过去5日收益率
        momentum = data['close'].pct_change(5)
        # 计算成交量变化：过去5日平均成交量 vs 过去20日平均成交量
        vol_ma5 = data['volume'].rolling(5).mean()
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = vol_ma5 / (vol_ma20 + 1e-10)
        # 判断价格创新高：当前收盘价是否高于过去20日最高价？
        new_high = data['close'] > data['close'].rolling(20).max().shift(1)
        # 判断价格创新低
        new_low = data['close'] < data['close'].rolling(20).min().shift(1)
        # 构建信号：
        # 1. 如果创新高但成交量萎缩(vol_ratio<0.8)，负信号
        # 2. 如果创新低且成交量放大(vol_ratio>1.2)，负信号
        # 3. 如果动量负且成交量放大，额外负信号
        # 综合得分
        signal = pd.Series(0.0, index=data.index)
        signal -= (new_high & (vol_ratio < 0.8)).astype(float) * 1.0
        signal -= (new_low & (vol_ratio > 1.2)).astype(float) * 1.0
        signal -= ( (momentum < -0.02) & (vol_ratio > 1.5) ).astype(float) * 0.8
        # 加入正向信号：动量强且缩量上涨
        signal += ( (momentum > 0.03) & (vol_ratio < 0.6) ).astype(float) * 0.5
        # 归一化到[-1,1]
        result = signal.clip(-1, 1)
        # 避免未来信息：shift(1)
        result = result.shift(1)
        result = result.fillna(0)
        return result
