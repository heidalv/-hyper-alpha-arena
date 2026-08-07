"""
Feature Engineering - 特征工程模块

提供因子组合和交互项计算：
1. 因子标准化与正规化
2. 交互特征生成
3. 多项式特征
4. 时序特征滚动计算
5. 特征选择与降维

Author: Hyper-Alpha-Arena
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import logging
from collections import deque

logger = logging.getLogger(__name__)


class NormalizationType(str, Enum):
    """标准化类型"""
    ZSCORE = "zscore"
    MINMAX = "minmax"
    ROBUST = "robust"
    RANK = "rank"
    PERCENTILE = "percentile"


class InteractionType(str, Enum):
    """交互特征类型"""
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    ADD = "add"
    SUBTRACT = "subtract"
    RATIO_DIFF = "ratio_diff"


@dataclass
class FeatureConfig:
    """特征工程配置"""
    # 标准化配置
    normalization_type: NormalizationType = NormalizationType.ZSCORE
    normalization_window: int = 100  # 滚动窗口大小
    
    # 交互特征配置
    enable_interactions: bool = True
    max_interaction_degree: int = 2
    interaction_pairs: List[Tuple[str, str]] = field(default_factory=list)
    
    # 多项式特征
    enable_polynomial: bool = False
    polynomial_degree: int = 2
    
    # 时序特征
    enable_time_features: bool = True
    time_windows: List[int] = field(default_factory=lambda: [5, 10, 20, 60])
    
    # 特征选择
    enable_feature_selection: bool = True
    selection_method: str = "importance"  # importance, correlation, variance
    max_features: int = 50
    min_importance: float = 0.01
    max_correlation: float = 0.85


@dataclass
class FeatureResult:
    """特征工程结果"""
    features: Dict[str, float]
    normalized_features: Dict[str, float]
    interaction_features: Dict[str, float]
    time_features: Dict[str, float]
    selected_features: List[str]
    feature_importance: Dict[str, float]
    timestamp: datetime = field(default_factory=datetime.now)


class FeatureNormalizer:
    """特征标准化器"""
    
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.history: Dict[str, deque] = {}
        self.stats: Dict[str, Dict[str, float]] = {}
    
    def update(self, feature_name: str, value: float) -> None:
        """更新特征历史"""
        if feature_name not in self.history:
            self.history[feature_name] = deque(maxlen=self.window_size)
        
        self.history[feature_name].append(value)
        self._update_stats(feature_name)
    
    def _update_stats(self, feature_name: str) -> None:
        """更新统计量"""
        values = list(self.history[feature_name])
        if len(values) < 2:
            return
        
        arr = np.array(values)
        self.stats[feature_name] = {
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "median": float(np.median(arr)),
            "q25": float(np.percentile(arr, 25)),
            "q75": float(np.percentile(arr, 75)),
        }
    
    def normalize(
        self,
        feature_name: str,
        value: float,
        method: NormalizationType = NormalizationType.ZSCORE
    ) -> float:
        """标准化单个特征"""
        if feature_name not in self.stats:
            return value
        
        stats = self.stats[feature_name]
        
        if method == NormalizationType.ZSCORE:
            std = stats["std"]
            if std == 0:
                return 0.0
            return (value - stats["mean"]) / std
        
        elif method == NormalizationType.MINMAX:
            range_val = stats["max"] - stats["min"]
            if range_val == 0:
                return 0.5
            return (value - stats["min"]) / range_val
        
        elif method == NormalizationType.ROBUST:
            iqr = stats["q75"] - stats["q25"]
            if iqr == 0:
                return 0.0
            return (value - stats["median"]) / iqr
        
        elif method == NormalizationType.RANK:
            # 简化的排名标准化
            values = list(self.history.get(feature_name, []))
            if not values:
                return 0.5
            rank = sum(1 for v in values if v <= value)
            return rank / len(values)
        
        elif method == NormalizationType.PERCENTILE:
            values = list(self.history.get(feature_name, []))
            if not values:
                return 50.0
            percentile = sum(1 for v in values if v <= value) / len(values) * 100
            return percentile
        
        return value
    
    def normalize_batch(
        self,
        features: Dict[str, float],
        method: NormalizationType = NormalizationType.ZSCORE
    ) -> Dict[str, float]:
        """批量标准化"""
        # 先更新历史
        for name, value in features.items():
            self.update(name, value)
        
        # 然后标准化
        return {
            name: self.normalize(name, value, method)
            for name, value in features.items()
        }


class InteractionFeatureGenerator:
    """交互特征生成器"""
    
    def __init__(self, config: FeatureConfig):
        self.config = config
        
        # 预定义的有效交互对
        self.default_interactions = [
            # 动量与波动率
            ("rsi", "atr_ratio"),
            ("macd_signal", "volatility"),
            # 价格与成交量
            ("price_change", "volume_ratio"),
            ("momentum", "obv_change"),
            # 趋势与反转
            ("trend_strength", "mean_reversion"),
            ("adx", "rsi"),
            # 市场微结构
            ("bid_ask_spread", "volume"),
            ("funding_rate", "oi_change"),
        ]
    
    def generate(self, features: Dict[str, float]) -> Dict[str, float]:
        """生成交互特征"""
        if not self.config.enable_interactions:
            return {}
        
        interaction_features = {}
        
        # 使用配置的交互对或默认交互对
        pairs = self.config.interaction_pairs or self.default_interactions
        
        for f1_name, f2_name in pairs:
            if f1_name in features and f2_name in features:
                f1_val = features[f1_name]
                f2_val = features[f2_name]
                
                # 乘积交互
                interaction_features[f"{f1_name}_x_{f2_name}"] = f1_val * f2_val
                
                # 比率交互（避免除零）
                if abs(f2_val) > 1e-8:
                    interaction_features[f"{f1_name}_div_{f2_name}"] = f1_val / f2_val
                
                # 差值交互
                interaction_features[f"{f1_name}_minus_{f2_name}"] = f1_val - f2_val
        
        # 生成同类因子的聚合特征
        interaction_features.update(self._generate_category_aggregations(features))
        
        return interaction_features
    
    def _generate_category_aggregations(self, features: Dict[str, float]) -> Dict[str, float]:
        """生成类别聚合特征"""
        aggregations = {}
        
        # 按前缀分组
        categories = {}
        for name, value in features.items():
            prefix = name.split("_")[0]
            if prefix not in categories:
                categories[prefix] = []
            categories[prefix].append(value)
        
        # 计算聚合
        for category, values in categories.items():
            if len(values) >= 2:
                arr = np.array(values)
                aggregations[f"{category}_mean"] = float(np.mean(arr))
                aggregations[f"{category}_std"] = float(np.std(arr))
                aggregations[f"{category}_range"] = float(np.max(arr) - np.min(arr))
        
        return aggregations


class TimeSeriesFeatureGenerator:
    """时序特征生成器"""
    
    def __init__(self, config: FeatureConfig):
        self.config = config
        self.history: Dict[str, deque] = {}
        self.max_window = max(config.time_windows) if config.time_windows else 60
    
    def update(self, features: Dict[str, float]) -> None:
        """更新特征历史"""
        for name, value in features.items():
            if name not in self.history:
                self.history[name] = deque(maxlen=self.max_window)
            self.history[name].append(value)
    
    def generate(self, features: Dict[str, float]) -> Dict[str, float]:
        """生成时序特征"""
        if not self.config.enable_time_features:
            return {}
        
        self.update(features)
        time_features = {}
        
        for name in features:
            history = list(self.history.get(name, []))
            if len(history) < 3:
                continue
            
            for window in self.config.time_windows:
                if len(history) >= window:
                    window_data = history[-window:]
                    arr = np.array(window_data)
                    
                    # 变化率
                    time_features[f"{name}_change_{window}"] = (
                        (arr[-1] - arr[0]) / abs(arr[0]) if abs(arr[0]) > 1e-8 else 0.0
                    )
                    
                    # 移动平均
                    time_features[f"{name}_ma_{window}"] = float(np.mean(arr))
                    
                    # 移动标准差
                    time_features[f"{name}_std_{window}"] = float(np.std(arr))
                    
                    # 趋势（简单线性回归斜率）
                    if len(arr) >= 3:
                        x = np.arange(len(arr))
                        slope = np.polyfit(x, arr, 1)[0]
                        time_features[f"{name}_trend_{window}"] = float(slope)
                    
                    # 动量
                    time_features[f"{name}_momentum_{window}"] = float(arr[-1] - arr[0])
        
        return time_features


class FeatureSelector:
    """特征选择器"""
    
    def __init__(self, config: FeatureConfig):
        self.config = config
        self.feature_importance: Dict[str, float] = {}
        self.feature_correlations: Dict[str, Dict[str, float]] = {}
    
    def update_importance(self, feature_name: str, importance: float) -> None:
        """更新特征重要性"""
        self.feature_importance[feature_name] = importance
    
    def update_importance_batch(self, importance: Dict[str, float]) -> None:
        """批量更新特征重要性"""
        self.feature_importance.update(importance)
    
    def calculate_correlations(self, features_history: Dict[str, List[float]]) -> None:
        """计算特征相关性"""
        feature_names = list(features_history.keys())
        n_features = len(feature_names)
        
        for i, f1 in enumerate(feature_names):
            if f1 not in self.feature_correlations:
                self.feature_correlations[f1] = {}
            
            for j, f2 in enumerate(feature_names):
                if i >= j:
                    continue
                
                values1 = features_history[f1]
                values2 = features_history[f2]
                
                if len(values1) == len(values2) and len(values1) > 3:
                    corr = np.corrcoef(values1, values2)[0, 1]
                    self.feature_correlations[f1][f2] = float(corr) if not np.isnan(corr) else 0.0
    
    def select(
        self,
        features: Dict[str, float],
        method: str = "importance"
    ) -> Tuple[List[str], Dict[str, float]]:
        """选择特征"""
        if not self.config.enable_feature_selection:
            return list(features.keys()), self.feature_importance
        
        if method == "importance":
            return self._select_by_importance(features)
        elif method == "correlation":
            return self._select_by_correlation(features)
        elif method == "variance":
            return self._select_by_variance(features)
        else:
            return list(features.keys()), self.feature_importance
    
    def _select_by_importance(self, features: Dict[str, float]) -> Tuple[List[str], Dict[str, float]]:
        """基于重要性选择"""
        # 按重要性排序
        sorted_features = sorted(
            [(name, self.feature_importance.get(name, 0.01))
             for name in features],
            key=lambda x: x[1],
            reverse=True
        )
        
        # 筛选
        selected = []
        for name, importance in sorted_features:
            if importance >= self.config.min_importance:
                selected.append(name)
            if len(selected) >= self.config.max_features:
                break
        
        return selected, dict(sorted_features)
    
    def _select_by_correlation(self, features: Dict[str, float]) -> Tuple[List[str], Dict[str, float]]:
        """基于相关性选择（去除高相关特征）"""
        selected = list(features.keys())
        removed = set()
        
        for f1 in self.feature_correlations:
            if f1 in removed:
                continue
            for f2, corr in self.feature_correlations[f1].items():
                if f2 in removed:
                    continue
                if abs(corr) > self.config.max_correlation:
                    # 移除重要性较低的特征
                    imp1 = self.feature_importance.get(f1, 0.01)
                    imp2 = self.feature_importance.get(f2, 0.01)
                    to_remove = f2 if imp1 >= imp2 else f1
                    removed.add(to_remove)
        
        selected = [f for f in selected if f not in removed]
        return selected[:self.config.max_features], self.feature_importance
    
    def _select_by_variance(self, features: Dict[str, float]) -> Tuple[List[str], Dict[str, float]]:
        """基于方差选择（去除低方差特征）"""
        # 这里简化处理，实际应使用历史方差
        return list(features.keys())[:self.config.max_features], self.feature_importance


class FeatureEngineer:
    """特征工程主类"""
    
    def __init__(self, config: Optional[FeatureConfig] = None):
        self.config = config or FeatureConfig()
        self.normalizer = FeatureNormalizer(self.config.normalization_window)
        self.interaction_generator = InteractionFeatureGenerator(self.config)
        self.time_generator = TimeSeriesFeatureGenerator(self.config)
        self.selector = FeatureSelector(self.config)
        
        # 默认特征重要性（可通过学习更新）
        self._init_default_importance()
    
    def _init_default_importance(self) -> None:
        """初始化默认特征重要性"""
        default_importance = {
            # 动量因子
            "rsi": 0.15,
            "macd": 0.12,
            "macd_signal": 0.10,
            "momentum": 0.08,
            "roc": 0.07,
            # 趋势因子
            "adx": 0.14,
            "trend_strength": 0.12,
            "supertrend": 0.10,
            # 波动率因子
            "atr": 0.13,
            "volatility": 0.11,
            "bb_width": 0.09,
            # 成交量因子
            "volume_ratio": 0.10,
            "obv": 0.08,
            "cvd": 0.07,
            # 市场微结构
            "funding_rate": 0.12,
            "oi_change": 0.10,
            "liquidation_ratio": 0.08,
        }
        self.selector.update_importance_batch(default_importance)
    
    def process(self, raw_features: Dict[str, float]) -> FeatureResult:
        """处理原始特征，生成工程化特征"""
        # 1. 标准化
        normalized = self.normalizer.normalize_batch(
            raw_features,
            self.config.normalization_type
        )
        
        # 2. 生成交互特征
        interactions = self.interaction_generator.generate(raw_features)
        
        # 3. 生成时序特征
        time_features = self.time_generator.generate(raw_features)
        
        # 4. 合并所有特征
        all_features = {
            **raw_features,
            **normalized,
            **interactions,
            **time_features
        }
        
        # 5. 特征选择
        selected, importance = self.selector.select(
            all_features,
            self.config.selection_method
        )
        
        return FeatureResult(
            features=raw_features,
            normalized_features=normalized,
            interaction_features=interactions,
            time_features=time_features,
            selected_features=selected,
            feature_importance=importance
        )
    
    def get_selected_features(
        self,
        raw_features: Dict[str, float],
        top_n: int = 20
    ) -> Dict[str, float]:
        """获取选定的顶部特征"""
        result = self.process(raw_features)
        
        # 按重要性排序并返回top_n
        sorted_features = sorted(
            [(name, result.features.get(name, result.normalized_features.get(name, 0.0)))
             for name in result.selected_features],
            key=lambda x: result.feature_importance.get(x[0], 0),
            reverse=True
        )[:top_n]
        
        return dict(sorted_features)
    
    def update_importance_from_performance(
        self,
        feature_performance: Dict[str, float]
    ) -> None:
        """根据性能表现更新特征重要性"""
        self.selector.update_importance_batch(feature_performance)
        logger.info(f"Updated feature importance for {len(feature_performance)} features")


# 全局实例
_feature_engineer: Optional[FeatureEngineer] = None


def get_feature_engineer() -> FeatureEngineer:
    """获取特征工程器实例"""
    global _feature_engineer
    if _feature_engineer is None:
        _feature_engineer = FeatureEngineer()
    return _feature_engineer


def process_features(raw_features: Dict[str, float]) -> FeatureResult:
    """处理原始特征"""
    return get_feature_engineer().process(raw_features)


def get_top_features(
    raw_features: Dict[str, float],
    top_n: int = 20
) -> Dict[str, float]:
    """获取顶部特征"""
    return get_feature_engineer().get_selected_features(raw_features, top_n)
