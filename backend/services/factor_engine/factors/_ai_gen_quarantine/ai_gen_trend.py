"""AI因子: 趋势强度指数 | 置信:60% | 基于线性回归R²度量最近20周期价格趋势的确定性，结合方向信息。计算close对时间的回归，取R²乘以方向符号（斜率正为+1，负为-1），再归一化到[-1,1]。高绝对值表示强趋势（不论方向），低值表示无趋势或震荡，容易造成止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthIndex(BaseFactor):
    """基于线性回归R²度量最近20周期价格趋势的确定性，结合方向信息。计算close对时间的回归，取R²乘以方向符号（斜率正为+1，负为-1），再归一化到[-1,1]。高绝对值表示强趋势（不论方向），低值表示无趋势或震荡，容易造成止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend",
            name="Trend Strength Index",
            display_name="趋势强度指数",
            description="基于线性回归R²度量最近20周期价格趋势的确定性，结合方向信息。计算close对时间的回归，取R²乘以方向符号（斜率正为+1，负为-1），再归一化到[-1,1]。高绝对值表示强趋势（不论方向），低值表示无趋势或震荡，容易造成止损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        periods = 20
        # 定义时间索引
        x = np.arange(periods)
        def reg_r2(series):
            if len(series) < periods:
                return np.nan
            y = series.values
            if np.std(y) < 1e-8:
                return 0.0
            # 线性回归
            A = np.vstack([x, np.ones(len(x))]).T
            slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
            y_pred = slope * x + intercept
            ss_res = np.sum((y - y_pred)**2)
            ss_tot = np.sum((y - np.mean(y))**2)
            r2 = 1 - ss_res / (ss_tot + 1e-10)
            # 方向符号
            sign = 1 if slope > 0 else -1
            return sign * r2
        result = close.rolling(periods, min_periods=periods).apply(reg_r2, raw=False)
        # 归一化到[-1,1] (r2本来在0~1，乘以符号后范围-1~1)
        result = result.fillna(0.0)
        # 限制极端值
        result = np.clip(result, -1.0, 1.0)
        return pd.Series(result, index=data.index, name='ai_gen_trend')
