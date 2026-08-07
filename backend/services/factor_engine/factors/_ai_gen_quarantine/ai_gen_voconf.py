"""AI因子: 成交量确认 | 置信:60% | 检测价格变动与成交量的配合程度。计算价格变化方向与成交量变化方向的一致性：当价格上涨且成交量放大时为正，价格下跌且成交量放大时为负，而价格与成交量背离时趋近0。指标通过对数收益率与成交量变化率的乘积进行平滑，再归一化到[-1,1]。用于识别突破或反转的有效性，避免无成交量支持的假动作。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumeconfirmation(BaseFactor):
    """检测价格变动与成交量的配合程度。计算价格变化方向与成交量变化方向的一致性：当价格上涨且成交量放大时为正，价格下跌且成交量放大时为负，而价格与成交量背离时趋近0。指标通过对数收益率与成交量变化率的乘积进行平滑，再归一化到[-1,1]。用于识别突破或反转的有效性，避免无成交量支持的假动作。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voconf",
            name="VolumeConfirmation",
            display_name="成交量确认",
            description="检测价格变动与成交量的配合程度。计算价格变化方向与成交量变化方向的一致性：当价格上涨且成交量放大时为正，价格下跌且成交量放大时为负，而价格与成交量背离时趋近0。指标通过对数收益率与成交量变化率的乘积进行平滑，再归一化到[-1,1]。用于识别突破或反转的有效性，避免无成交量支持的假动作。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].values
        volume = data['volume'].values
        # 计算价格对数收益率和成交量变化率
        pct_ret = np.diff(np.log(close + 1e-10))
        vol_change = np.diff(np.log(volume + 1e-10))
        # 乘积：同号为正，异号为负
        raw = pct_ret * vol_change
        # 滚动窗口平滑（例如20期）并归一化
        window = 20
        smoothed = pd.Series(raw).rolling(window, min_periods=1).mean().values
        # 归一化到[-1,1]：使用tanh压缩
        std = np.nanstd(smoothed) + 1e-10
        normalized = np.tanh(smoothed / std)
        # 前补NaN对齐长度
        result = np.append(np.nan, normalized)
        return pd.Series(result, index=data.index)
