"""
DatasetCache + infer/learn processor 分离（P1.8，对标 Qlib DataHandler）。

目标（方案 §P1.8）：补齐"仅 ExpressionCache"的缺口。
    - DatasetCache：按 (handler_config, segment) 缓存整个 Dataset（特征+标签矩阵），
      重复回测同配置命中缓存（Qlib 式）。
    - processor 分离：
        infer_processors: 推理路径用（归一化/fillna），保证线上特征与训练同分布。
        learn_processors: 学习路径用（DropnaLabel + 横截面 z-score 标签）。

设计（Qlib infer/learn processor split）：
    特征在推理时（线上）和训练时用同一套 infer_processors 保证一致；
    标签仅在训练侧经 learn_processors 处理（横截面归一化、去 NaN）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pandas as pd

# ==================== Processors ====================

@dataclass
class Processor:
    """数据处理器基类。"""
    name: str

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError


class Fillna(Processor):
    """前向/后向填充 NaN。"""
    def __init__(self, method: str = "ffill"):
        super().__init__("fillna")
        self.method = method

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.method == "ffill":
            return df.ffill().fillna(0.0)
        elif self.method == "bfill":
            return df.bfill().fillna(0.0)
        return df.fillna(0.0)


class ZScoreNorm(Processor):
    """Z-score 归一化（按列，时间序列）。"""
    def __init__(self, fit_mean=None, fit_std=None):
        super().__init__("zscore_norm")
        self._fit_mean = fit_mean
        self._fit_std = fit_std

    def fit(self, df: pd.DataFrame) -> "ZScoreNorm":
        self._fit_mean = df.mean()
        self._fit_std = df.std().replace(0, 1.0)
        return self

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        if self._fit_mean is None:
            self.fit(df)
        return (df - self._fit_mean) / self._fit_std.replace(0, 1.0)


class RobustScale(Processor):
    """Robust 归一化（中位数 + IQR，抗异常值）。"""
    def __init__(self):
        super().__init__("robust_scale")
        self._med = None
        self._iqr = None

    def fit(self, df: pd.DataFrame) -> "RobustScale":
        self._med = df.median()
        self._iqr = (df.quantile(0.75) - df.quantile(0.25)).replace(0, 1.0)
        return self

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        if self._med is None:
            self.fit(df)
        return (df - self._med) / self._iqr


class CSZScoreNorm(Processor):
    """横截面 Z-score（每行/每时刻跨品种归一化）。用于标签。"""
    def __init__(self):
        super().__init__("cs_zscore_norm")

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        # df 假设 index=时间，columns=品种，或单列则无操作
        if df.ndim == 2 and df.shape[1] > 1:
            mean = df.mean(axis=1)
            std = df.std(axis=1).replace(0, 1.0)
            return df.sub(mean, axis=0).div(std, axis=0)
        return df


class DropnaLabel(Processor):
    """丢弃标签为 NaN 的行（仅学习路径）。"""
    def __init__(self, label_col: str = "label"):
        super().__init__("dropna_label")
        self.label_col = label_col

    def __call__(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.label_col in df.columns:
            return df.dropna(subset=[self.label_col])
        return df


# ==================== DataHandler（infer/learn 分离） ====================

@dataclass
class DataHandler:
    """
    数据处理器：组织 infer_processors（特征，推理+训练共用）和 learn_processors（标签，仅训练）。

    对标 Qlib DataHandlerLP 的 infer/learn processor 分离：
        - infer: 特征归一化，fit 在训练集、apply 到全集（防泄漏）
        - learn: 标签处理（DropnaLabel + CSZScoreNorm）
    """
    infer_processors: list[Processor] = field(default_factory=list)
    learn_processors: list[Processor] = field(default_factory=list)
    label_col: str = "label"
    _fitted: bool = False

    def fit(self, df: pd.DataFrame) -> "DataHandler":
        """在训练段 fit infer_processors（学归一化参数）。"""
        out = df
        for p in self.infer_processors:
            if hasattr(p, "fit"):
                p.fit(out)
            out = p(out)
        self._fitted = True
        return self

    def process_infer(self, df: pd.DataFrame) -> pd.DataFrame:
        """推理路径：仅 apply infer_processors（用已 fit 的参数）。"""
        out = df
        for p in self.infer_processors:
            out = p(out)
        return out

    def process_learn(self, df: pd.DataFrame) -> pd.DataFrame:
        """学习路径：先 infer 处理特征，再 learn 处理标签。"""
        out = self.process_infer(df)
        for p in self.learn_processors:
            out = p(out)
        return out

    def split_features_label(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        """分离特征矩阵和标签列。"""
        if self.label_col in df.columns:
            return df.drop(columns=[self.label_col]), df[self.label_col]
        return df, pd.Series(dtype=float, index=df.index)


# ==================== DatasetCache（Qlib 式） ====================

class DatasetCache:
    """
    数据集级缓存。键 = (handler_config_hash, segment)。

    适合重复回测：同一 handler 配置 + 同一时间段，特征矩阵命中缓存无需重算。
    """

    def __init__(self, max_entries: int = 64):
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}
        self._max = max_entries
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _config_hash(handler: DataHandler, segment: str) -> str:
        """规范化 handler 配置 → 哈希（处理器名+参数）。"""
        cfg = {
            "infer": [{"name": p.name, "params": getattr(p, "__dict__", {})} for p in handler.infer_processors],
            "learn": [{"name": p.name, "params": getattr(p, "__dict__", {})} for p in handler.learn_processors],
        }
        s = json.dumps(cfg, sort_keys=True, default=str)
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]

    def get_or_process(
        self,
        handler: DataHandler,
        raw_df: pd.DataFrame,
        segment: str,
        *,
        learn: bool = False,
    ) -> pd.DataFrame:
        """命中缓存则返回，否则处理并缓存。"""
        chash = self._config_hash(handler, segment)
        mode = "learn" if learn else "infer"
        key = (chash, f"{segment}::{mode}")
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        self.misses += 1
        result = handler.process_learn(raw_df) if learn else handler.process_infer(raw_df)
        if len(self._cache) >= self._max:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = result
        return result

    def clear(self) -> None:
        self._cache.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {"entries": len(self._cache), "hits": self.hits, "misses": self.misses,
                "hit_rate": self.hits / total if total else 0.0}
