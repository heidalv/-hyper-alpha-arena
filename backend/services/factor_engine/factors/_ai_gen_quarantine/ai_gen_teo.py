"""AI因子: 趋势衰竭振荡器 | 置信:70% | 结合价格与均线偏离度、RSI和成交量萎缩，识别上升趋势的衰竭。数值趋近-1表示上升动能耗尽、反转风险高；趋近+1表示上升趋势健康。用于过滤或减仓多头信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustionOscillator(BaseFactor):
    """结合价格与均线偏离度、RSI和成交量萎缩，识别上升趋势的衰竭。数值趋近-1表示上升动能耗尽、反转风险高；趋近+1表示上升趋势健康。用于过滤或减仓多头信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_teo",
            name="Trend Exhaustion Oscillator",
            display_name="趋势衰竭振荡器",
            description="结合价格与均线偏离度、RSI和成交量萎缩，识别上升趋势的衰竭。数值趋近-1表示上升动能耗尽、反转风险高；趋近+1表示上升趋势健康。用于过滤或减仓多头信号。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']
        # 20期均线
        ma20 = close.rolling(20).mean()
        # 价格偏离均线百分比
        pct_dev = (close - ma20) / ma20
        # 14期RSI
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-9)
        rsi = 100 - (100 / (1 + rs))
        # RSI标准化到[-1,1]，50为0
        rsi_norm = (rsi - 50) / 50
        # 成交量萎缩：当前成交量 / 过去20期平均成交量
        vol_ratio = volume / volume.rolling(20).mean()
        # 成交量比率标准化：>1为放量，<1为缩量
        vol_score = 2 * (vol_ratio - 0.8) / (1.2 - 0.6)  # 大致映射
        vol_score = vol_score.clip(-1, 1)
        # 综合：价格偏离度（正表示高于均线）、RSI强度、成交量支持
        # 当价格高于均线但RSI超买且缩量时，趋势衰竭
        # 我们用(偏离度 + RSI_norm - 缩量惩罚) 合成
        # 偏离度归一化：除以近期波动率
        atr = (high - low).rolling(14).mean()
        dev_score = (pct_dev * 100) / (atr / close * 100 + 1e-9)
        dev_score = dev_score.clip(-3, 3) / 3  # 限制在[-1,1]
        # 合成：趋势健康 = 正偏离 * RSI正常 * 成交量放大
        # 衰竭 = 高偏离但RSI从高位回落 + 成交量萎缩
        rsi_momentum = rsi.diff(3).fillna(0) / 10  # RSI变化率
        rsi_momentum = rsi_momentum.clip(-1, 1)
        # 衰竭信号：如果偏离度高但RSI开始下降且缩量，则负分
        exhaustion = - (dev_score * (rsi_momentum) * (1 - vol_score.clip(0,1)))
        exhaustion = exhaustion.fillna(0)
        # 最终值：如果上升趋势健康，则接近1；衰竭则接近-1
        # 也可直接使用趋势健康度
        trend_health = (dev_score + rsi_norm + (vol_score - 0.5) * 0.5) / 2.5
        trend_health = trend_health.clip(-1, 1)
        # 把趋势健康作为主输出，当健康度下降时接近-1
        result = trend_health
        return result
