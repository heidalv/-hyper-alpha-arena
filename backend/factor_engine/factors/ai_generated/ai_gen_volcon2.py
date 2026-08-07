"""AI因子: 量价背离 | 置信:60% | 检测价格变动与成交量之间的背离。例如价格下跌但成交量萎缩，表明下跌动能不足，容易引发反弹导致做空止损；反之亦然。背离强度映射到[-1,1]，正背离（价格涨量缩）给出负信号，负背离（价格跌量缩）给出正信号，但这里专注于避免亏损，统一用绝对值？根据错误模式，做空亏损多，所以特别关注下跌缩量反弹风险。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class VolumePriceDivergence(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volcon2", name="VolumePriceDivergence",
        display_name="量价背离", description="检测价格变动与成交量之间的背离。例如价格下跌但成交量萎缩，表明下跌动能不足，容易引发反弹导致做空止损；反之亦然。背离强度映射到[-1,1]，正背离（价格涨量缩）给出负信号，负背离（价格跌量缩）给出正信号，但这里专注于避免亏损，统一用绝对值？根据错误模式，做空亏损多，所以特别关注下跌缩量反弹风险。",
        category="technical", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data: pd.DataFrame) -> pd.Series:
    close = data['close']
    volume = data['volume']
    # 价格变化率
    price_ret = close.pct_change(5)
    # 成交量变化率（平滑）
    vol_ma = volume.rolling(5).mean()
    vol_ret = vol_ma.pct_change(5)
    # 背离：价格下跌（负ret）但成交量也下跌（负ret） => 量价同向，不背离；价格下跌但成交量上升 => 背离空头风险大？实际错误是做空亏损，可能是下跌缩量反弹，即价格跌量缩，反弹风险大，所以检测price_ret < 0 and vol_ret < 0 时给出负信号
    # 构造背离指标
    sign_price = np.sign(price_ret)
    sign_vol = np.sign(vol_ret)
    # 当价格下跌且成交量下跌时，认为潜在反弹风险，因子负向
    cond = (price_ret < 0) & (vol_ret < 0)
    magnitude = (price_ret.abs() + vol_ret.abs()) / 2
    result = pd.Series(0.0, index=data.index)
    result[cond] = -magnitude[cond]  # 负值表示要规避
    # 同理，价格上涨且成交量下跌，做多风险？但亏损少，暂不考虑
    # 归一化到[-1,1]：滚动窗口百分比
    rolling_max = result.abs().rolling(100).max()
    result = result / rolling_max.replace(0, np.nan)
    result = result.clip(-1, 1)
    return result.fillna(0)
