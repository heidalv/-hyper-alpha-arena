"""AI因子: 市场状态不确定性 | 置信:60% | 通过价格路径的混沌程度（基于Hurst指数近似）和波动率聚类特征，量化当前市场处于未知状态的概率。当因子接近-1表示高度不确定性（容易触发各类止损），+1表示趋势明确。适用于规避未知regime风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertaintyIndicator(BaseFactor):
    """通过价格路径的混沌程度（基于Hurst指数近似）和波动率聚类特征，量化当前市场处于未知状态的概率。当因子接近-1表示高度不确定性（容易触发各类止损），+1表示趋势明确。适用于规避未知regime风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_un",
            name="Regime Uncertainty Indicator",
            display_name="市场状态不确定性",
            description="通过价格路径的混沌程度（基于Hurst指数近似）和波动率聚类特征，量化当前市场处于未知状态的概率。当因子接近-1表示高度不确定性（容易触发各类止损），+1表示趋势明确。适用于规避未知regime风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 使用日内分形维近似：计算对数收益率序列的短期波动模式
        log_ret = np.log(data['close'] / data['close'].shift(1))
        # 计算20周期滚动标准差
        vol_20 = log_ret.rolling(20).std()
        # 计算波动率变化率（波动率聚类）
        vol_chg_5 = vol_20.pct_change(5)
        # 用移动窗口计算局部趋势的序列相关（帮助识别随机游走）
        # 构造一个简单的Hurst近似：R/S统计
        def hurst_approx(series):
            if len(series) < 20:
                return 0.5
            # 使用均值调整累积离差
            mu = series.mean()
            y = series - mu
            z = np.cumsum(y)
            r = z.max() - z.min()
            s = series.std(ddof=1)
            if s == 0:
                return 0.5
            return np.log(r/s) / np.log(len(series))
        # 滚动计算21日Hurst
        hurst = log_ret.rolling(21).apply(hurst_approx, raw=True)
        # 信号：Hurst接近0.5且波动率变化激烈 => 不确定性高
        uncert = -np.abs(hurst - 0.5) * np.abs(vol_chg_5)
        # 归一化
        uncert = uncert / (uncert.abs().rolling(60).mean() + 1e-8)
        result = np.clip(uncert, -1, 1)
        result = result.fillna(0.0)
        return result
