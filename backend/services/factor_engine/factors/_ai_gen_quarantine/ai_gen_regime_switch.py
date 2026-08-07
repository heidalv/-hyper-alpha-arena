"""AI因子: 市场状态切换指标 | 置信:60% | 通过比较短期与长期价格趋势的偏离程度以及波动率变化，识别市场从趋势到震荡或反之的切换。当偏离度大且波动率扩张时，可能进入趋势；当偏离度缩小且波动率收缩时，可能进入震荡。输出值接近+1表示趋势增强，-1表示趋势衰减或震荡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeSwitchIndicator(BaseFactor):
    """通过比较短期与长期价格趋势的偏离程度以及波动率变化，识别市场从趋势到震荡或反之的切换。当偏离度大且波动率扩张时，可能进入趋势；当偏离度缩小且波动率收缩时，可能进入震荡。输出值接近+1表示趋势增强，-1表示趋势衰减或震荡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_switch",
            name="Regime Switch Indicator",
            display_name="市场状态切换指标",
            description="通过比较短期与长期价格趋势的偏离程度以及波动率变化，识别市场从趋势到震荡或反之的切换。当偏离度大且波动率扩张时，可能进入趋势；当偏离度缩小且波动率收缩时，可能进入震荡。输出值接近+1表示趋势增强，-1表示趋势衰减或震荡。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算短期和长期移动平均
        short_ma = data['close'].rolling(window=5).mean()
        long_ma = data['close'].rolling(window=20).mean()
        # 价格偏离度
        deviation = (short_ma - long_ma) / (data['close'] + 1e-10)
        # 波动率变化（使用ATR比率）
        tr = pd.concat([data['high'] - data['low'], abs(data['high'] - data['close'].shift(1)), abs(data['low'] - data['close'].shift(1))], axis=1).max(axis=1)
        atr_short = tr.rolling(window=5).mean()
        atr_long = tr.rolling(window=20).mean()
        vol_ratio = (atr_short - atr_long) / (atr_long + 1e-10)
        # 综合信号
        regime = deviation * 0.5 + vol_ratio * 0.5
        # 归一化到[-1,1]
        return np.clip(regime * 10, -1, 1)
