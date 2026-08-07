"""AI因子: 放量下跌确认 | 置信:60% | 检测价格下跌是否伴随成交量显著放大，放量下跌通常表明空头力量强，不适合做多。计算价格变化与成交量变化的组合信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmedDecline(BaseFactor):
    """检测价格下跌是否伴随成交量显著放大，放量下跌通常表明空头力量强，不适合做多。计算价格变化与成交量变化的组合信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_break",
            name="VolumeConfirmedDecline",
            display_name="放量下跌确认",
            description="检测价格下跌是否伴随成交量显著放大，放量下跌通常表明空头力量强，不适合做多。计算价格变化与成交量变化的组合信号。",
            category="volume",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 价格变化率
        ret = data['close'].pct_change()
        # 成交量变化率（相对过去20期均值）
        vol_ma = data['volume'].rolling(window=20).mean()
        vol_ratio = data['volume'] / vol_ma
        # 条件：价格下跌超过0.5% 且 成交量放大到1.5倍以上
        condition = (ret < -0.005) & (vol_ratio > 1.5)
        # 强度因子：跌幅越大、量比越大，负值越强
        strength = np.clip(np.abs(ret) * 100, 0, 1) * np.clip((vol_ratio - 1) / 2, 0, 1)
        result = np.where(condition, -strength, 0)
        return pd.Series(result, index=data.index).fillna(0)
