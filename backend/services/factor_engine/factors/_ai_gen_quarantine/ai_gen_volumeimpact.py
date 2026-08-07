"""AI因子: 量价背离冲击 | 置信:50% | 衡量价格变动与成交量配合程度。当价格上涨但成交量萎缩或价格下跌放量时，表明趋势不可持续。通过计算价格变化百分比的成交量加权移动平均与价格变化自身的相关性来识别弱势上涨。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Impact_Deficiency(BaseFactor):
    """衡量价格变动与成交量配合程度。当价格上涨但成交量萎缩或价格下跌放量时，表明趋势不可持续。通过计算价格变化百分比的成交量加权移动平均与价格变化自身的相关性来识别弱势上涨。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumeimpact",
            name="Volume Impact Deficiency",
            display_name="量价背离冲击",
            description="衡量价格变动与成交量配合程度。当价格上涨但成交量萎缩或价格下跌放量时，表明趋势不可持续。通过计算价格变化百分比的成交量加权移动平均与价格变化自身的相关性来识别弱势上涨。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变化
        ret = close.pct_change()
        # 成交量变化（标准化）
        vol_chg = volume.pct_change()
        # 滚动24期相关系数
        corr = ret.rolling(24).corr(vol_chg)
        # 期望正相关，负相关表示异常
        result = -corr.fillna(0)
        # 同时考虑上涨但量减：ret正且vol_chg负
        cond = (ret > 0) & (vol_chg < 0)
        penalty = cond.astype(float) * 0.5
        final = result - penalty
        final = np.clip(final, -1, 1)
        return final
