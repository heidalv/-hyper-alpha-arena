"""
FactorComputeAgent（L3，方案 §1.3 / §2.1）。

职责：K线 DataFrame → 契约 FactorVector。
    - 从 OHLCV DataFrame 提取字段（open/high/low/close/volume/returns + 衍生品）
    - 用 P1.1 表达式 DSL（FactorExpr.evaluate）计算活跃因子集
    - 输出 Lean 契约 FactorVector（带 expr_ids 可追溯）
    - ExpressionCache 加速重算（Qlib 式）

这是把"真实市场数据"接到"表达式因子引擎"的接入层。
Alpha 层只消费 FactorVector，不感知 OHLCV 细节。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from backend.services.contracts.types import FactorVector, Instrument
from backend.services.factor_engine.expr.parser import ExpressionCache, FactorExpr, get_default_cache, parse
from backend.services.factor_engine.perp_factors import PERP_FACTOR_EXPRS

logger = logging.getLogger(__name__)


# OHLCV DataFrame 列名 → DSL field 名的映射
DF_COLUMN_MAP: dict[str, str] = {
    "open": "open", "high": "high", "low": "low", "close": "close",
    "volume": "volume", "vol": "volume", "amount": "amount",
    "turnover": "turnover",
    # 衍生品（可能不在 K 线里，需外部注入）
    "funding_rate": "funding", "funding": "funding",
    "open_interest": "oi", "oi": "oi",
    "basis": "basis", "liquidation": "liquidation",
}


def kline_df_to_fields(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """
    K线 DataFrame → DSL fields dict。

    自动派生：
        - returns = pct_change(close)
        - vwap = （若缺）用 (high+low+close)/3 近似
    """
    fields: dict[str, np.ndarray] = {}
    for col, field_name in DF_COLUMN_MAP.items():
        if col in df.columns:
            fields[field_name] = np.asarray(df[col].values, dtype=float)

    # 派生 returns
    if "close" in fields and "returns" not in fields:
        close = fields["close"]
        rets = np.zeros_like(close)
        if len(close) > 1:
            rets[1:] = np.diff(close) / np.where(close[:-1] != 0, close[:-1], 1.0)
        fields["returns"] = rets

    # 派生 vwap（若缺）
    if "vwap" not in fields:
        if all(k in fields for k in ("high", "low", "close")):
            fields["vwap"] = (fields["high"] + fields["low"] + fields["close"]) / 3.0
        elif "close" in fields:
            fields["vwap"] = fields["close"].copy()

    return fields


@dataclass
class FactorComputeAgent:
    """
    K线 → FactorVector（活跃因子集计算）。

    用法：
        agent = FactorComputeAgent(instrument=inst)
        agent.register("my_alpha", expr_or_ast)
        fv = agent.compute(kline_df)  # → FactorVector
    """

    instrument: Instrument
    cache: ExpressionCache = field(default_factory=get_default_cache)
    _exprs: dict[str, FactorExpr] = field(default_factory=dict)

    def register(self, name: str, expr: FactorExpr | dict) -> None:
        """注册一个活跃因子（表达式或 AST）。"""
        if isinstance(expr, dict):
            expr = parse(expr)
        self._exprs[name] = expr

    def register_perp_defaults(self) -> None:
        """注册永续特化因子集（P1.7）。"""
        for name, ast in PERP_FACTOR_EXPRS.items():
            try:
                self.register(name, ast)
            except Exception as e:
                logger.debug(f"[FactorCompute] 跳过 {name}: {e}")

    def compute(
        self, df: pd.DataFrame, *, ts_ns: int = 0,
        window_id: str = "",
    ) -> FactorVector:
        """
        计算 FactorVector。

        df: OHLCV DataFrame（含 open/high/low/close/volume 等）。
        ts_ns: 快照时间戳（默认用 DataFrame 最后一行的 index）。
        window_id: 缓存窗口标识（同窗口重复算命中缓存）。
        """
        if ts_ns == 0:
            # 用 DataFrame 末行时间戳
            last_idx = df.index[-1] if len(df) > 0 else 0
            ts_ns = int(pd.Timestamp(last_idx).timestamp() * 1e9) if hasattr(last_idx, "timestamp") else 0

        fields = kline_df_to_fields(df)
        values: dict[str, float] = {}
        expr_ids: dict[str, str] = {}

        for name, expr in self._exprs.items():
            try:
                # 用缓存（同 expr+instrument+window 不重算）
                arr = self.cache.get_or_eval(expr, fields, self.instrument.symbol, window_id)
                # 取末值（最新因子值）
                val = float(arr[-1]) if len(arr) > 0 and np.isfinite(arr[-1]) else 0.0
                values[name] = val
                expr_ids[name] = expr.expr_id
            except Exception as e:
                logger.debug(f"[FactorCompute] 因子 {name} 计算失败: {e}")
                values[name] = 0.0
                expr_ids[name] = expr.expr_id

        return FactorVector(
            ts_ns=ts_ns, instrument=self.instrument,
            values=values, expr_ids=expr_ids,
        )

    def compute_series(self, df: pd.DataFrame, *, window_id: str = "") -> dict[str, np.ndarray]:
        """
        计算因子时间序列（用于回测/IC 评估，非末值）。

        返回 {factor_name: full_array}。
        """
        fields = kline_df_to_fields(df)
        series: dict[str, np.ndarray] = {}
        for name, expr in self._exprs.items():
            try:
                arr = self.cache.get_or_eval(expr, fields, self.instrument.symbol, window_id)
                series[name] = arr
            except Exception:
                series[name] = np.full(len(df), np.nan)
        return series

    def active_factors(self) -> list[str]:
        return list(self._exprs.keys())
