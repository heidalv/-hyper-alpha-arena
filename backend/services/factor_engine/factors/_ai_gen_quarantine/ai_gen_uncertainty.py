"""AI因子: 市场不确定指数 | 置信:55% | 基于价格波动率与趋势强度的背离，识别市场状态不明的高风险环境。当价格在均线附近频繁穿越且振幅较大时，信号接近-1，提示规避；当趋势明确时信号接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UncertaintyRegime(BaseFactor):
    """基于价格波动率与趋势强度的背离，识别市场状态不明的高风险环境。当价格在均线附近频繁穿越且振幅较大时，信号接近-1，提示规避；当趋势明确时信号接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_uncertainty",
            name="Uncertainty_Regime",
            display_name="市场不确定指数",
            description="基于价格波动率与趋势强度的背离，识别市场状态不明的高风险环境。当价格在均线附近频繁穿越且振幅较大时，信号接近-1，提示规避；当趋势明确时信号接近+1。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算20日标准差和20日简单移动均线
        std20 = data['close'].rolling(20).std()
        sma20 = data['close'].rolling(20).mean()
        # 价格偏离均线的绝对百分比
        deviation = (data['close'] - sma20).abs() / sma20
        # 归一化标准差（用当前close标准化）
        norm_vol = std20 / data['close']
        # 趋势强度：使用20日相关系数或ADX简化版本
        returns = data['close'].pct_change()
        trend_strength = returns.rolling(20).mean().abs() / (returns.rolling(20).std() + 1e-10)
        # 不确定性得分：高波动 + 低趋势强度 + 高价格穿越频率
        crossing = (data['close'] - sma20).apply(lambda x: 1 if abs(x) < 0.01 * sma20.iloc[0] else 0).rolling(5).sum() / 5.0
        # 组合：波动率越高、趋势越弱、穿越越多，不确定性越高
        raw = norm_vol * (1 - trend_strength.clip(0,1)) * crossing
        # 缩放到[-1,1]：使用rolling窗口的z-score或直接tanh
        from scipy.stats import zscore
        z = (raw - raw.rolling(100).mean()) / (raw.rolling(100).std() + 1e-10)
        result = np.clip(z * -0.5, -1, 1)  # 负值表示高风险不确定性
        return result.fillna(0)
