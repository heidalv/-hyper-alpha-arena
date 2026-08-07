"""AI因子: 未知状态适应性信号 | 置信:60% | 基于市场状态模糊性（低波动率+低趋势强度+成交量异常）设计自适应信号。计算过去20根K线的波动率（ATR/价格）、趋势强度（ADX简化版）和成交量变异系数。当三者均处于历史低分位（<30%）时，认为市场状态未知，此时利用短期反转逻辑：若价格下跌则做多，上涨则做空。信号强度由条件满足程度加权。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknown Regime Adaptive Signal(BaseFactor):
    """基于市场状态模糊性（低波动率+低趋势强度+成交量异常）设计自适应信号。计算过去20根K线的波动率（ATR/价格）、趋势强度（ADX简化版）和成交量变异系数。当三者均处于历史低分位（<30%）时，认为市场状态未知，此时利用短期反转逻辑：若价格下跌则做多，上涨则做空。信号强度由条件满足程度加权。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknownreg",
            name="Unknown Regime Adaptive Signal",
            display_name="未知状态适应性信号",
            description="基于市场状态模糊性（低波动率+低趋势强度+成交量异常）设计自适应信号。计算过去20根K线的波动率（ATR/价格）、趋势强度（ADX简化版）和成交量变异系数。当三者均处于历史低分位（<30%）时，认为市场状态未知，此时利用短期反转逻辑：若价格下跌则做多，上涨则做空。信号强度由条件满足程度加权。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            high = data['high']
            low = data['low']
            close = data['close']
            volume = data['volume']
            # 波动率: ATR/close
            tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
            atr = tr.rolling(20).mean()
            vola = atr / close
            # 趋势强度: 简单移动平均线斜率标准化
            ma = close.rolling(20).mean()
            slope = ma.diff(5) / ma.shift(5)
            trend_strength = slope.abs()
            # 成交量变异系数
            vol_std = volume.rolling(20).std()
            vol_mean = volume.rolling(20).mean()
            vol_cv = vol_std / vol_mean
            # 计算各指标30%分位数
            def rank_pct(series, window=50):
                return series.rolling(window).rank(pct=True)
            vola_rank = rank_pct(vola, 50)
            trend_rank = rank_pct(trend_strength, 50)
            vol_cv_rank = rank_pct(vol_cv, 50)
            # 状态未知条件：三者均低于0.3
            unknown = (vola_rank < 0.3) & (trend_rank < 0.3) & (vol_cv_rank < 0.3)
            # 短期反转信号：最近1日价格变化方向
            mom = close.diff()
            signal = pd.Series(0.0, index=data.index)
            signal[unknown & (mom < 0)] = 0.6   # 下跌时预期反弹
            signal[unknown & (mom > 0)] = -0.6  # 上涨时预期回落
            return signal
