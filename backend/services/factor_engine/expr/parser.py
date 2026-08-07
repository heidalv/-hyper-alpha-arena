"""
因子表达式 DSL — 解析器与求值器（P1.1）。

职责：
    - parse(expr_ast) -> FactorExpr：编译 JSON AST 为可执行表达式（含审计）。
    - expr.evaluate(fields) -> np.ndarray：在给定字段数据上求值。
    - expr_id(expr_ast) -> str：表达式规范化哈希（版本化追溯、去重、缓存键）。

设计要点（方案 §2.1）：
    - 先 audit 再编译（结构错误 + look-ahead 在 parse 期拦截）。
    - 求值递归遍历 AST，叶子为字段/常量，内部节点为算子。
    - ExpressionCache 按 (expr_id, instrument, ts_window) 缓存重算结果（Qlib 式）。
    - 无字符串 eval、无 import、无 IO —— 纯函数式，可安全运行任意 AST。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.services.factor_engine.expr.audit import AuditResult, audit
from backend.services.factor_engine.expr.ops import OP_REGISTRY


class ExprError(Exception):
    """表达式 DSL 错误（审计失败 / 求值错误）。"""


@dataclass(frozen=True)
class FactorExpr:
    """已编译的因子表达式。"""
    ast: dict
    expr_id: str
    source: str  # 原始 JSON 字符串（可读性/调试）

    def evaluate(self, fields: dict[str, np.ndarray]) -> np.ndarray:
        """在给定字段数据上求值。fields: {"close": array, "volume": array, ...}。"""
        return _eval_node(self.ast, fields)


def _canonical(node: Any) -> Any:
    """规范化 AST 节点用于哈希（排序 dict 键、确保确定性）。"""
    if isinstance(node, dict):
        return {k: _canonical(node[k]) for k in sorted(node)}
    if isinstance(node, list):
        return [_canonical(x) for x in node]
    return node


def expr_id(expr_ast: dict) -> str:
    """表达式规范化哈希（SHA256 前 16 位）。相同表达式 → 相同 id（去重/缓存键）。"""
    canon = json.dumps(_canonical(expr_ast), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def parse(expr_ast: dict, *, source: str | None = None) -> FactorExpr:
    """
    编译表达式 AST。

    参数：
        expr_ast: 表达式 dict，如 {"op":"rank","args":[{"op":"corr","args":[{"f":"vwap"},{"f":"volume"},{"c":5}]}]}
        source: 可选，原始 JSON 字符串（调试/日志）。
    返回：
        FactorExpr（已通过审计）。
    抛出：
        ExprError: 若审计失败（结构错误/look-ahead）。
    """
    if not isinstance(expr_ast, dict):
        raise ExprError(f"表达式必须是 dict，得到 {type(expr_ast).__name__}")
    result: AuditResult = audit(expr_ast)
    if not result.ok:
        raise ExprError(
            "表达式审计失败：" + "; ".join(result.errors)
        )
    return FactorExpr(
        ast=expr_ast,
        expr_id=expr_id(expr_ast),
        source=source or json.dumps(expr_ast, ensure_ascii=False, sort_keys=True),
    )


def _eval_node(node: Any, fields: dict[str, np.ndarray]) -> np.ndarray:
    """递归求值 AST 节点。"""
    if not isinstance(node, dict):
        raise ExprError(f"非法节点类型 {type(node).__name__}")

    # 字段叶子
    if "f" in node:
        fname = node["f"]
        if fname not in fields:
            raise ExprError(f"字段 '{fname}' 未在提供的数据中")
        return np.asarray(fields[fname], dtype=float).reshape(-1)

    # 常量叶子
    if "c" in node:
        return node["c"]  # 标量；算子内 _a1d 会处理

    # DeltaTime 叶子（解析为整数秒数；目前仅作标记，实际窗口用 c 节点）
    if "d" in node:
        return _parse_delta_time(node["d"])

    # 算子节点
    if "op" in node:
        op_name = node["op"]
        if op_name not in OP_REGISTRY:
            raise ExprError(f"未知算子 '{op_name}'（求值期）")
        _, fn = OP_REGISTRY[op_name]
        args = [_eval_node(a, fields) for a in node.get("args", [])]
        try:
            return fn(*args)
        except Exception as e:
            raise ExprError(f"算子 '{op_name}' 求值失败: {e!r}") from e

    raise ExprError(f"无法识别的节点：键={list(node.keys())}")


def _parse_delta_time(s: str) -> int:
    """将 '5m'/'1h'/'1d' 解析为秒数（占位实现，供下游窗口映射）。"""
    s = s.strip()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if not s:
        return 0
    unit = s[-1].lower()
    if unit not in units:
        return 0
    try:
        return int(float(s[:-1]) * units[unit])
    except ValueError:
        return 0


# ==================== ExpressionCache（P1.1 基础版，Qlib 式） ====================

class ExpressionCache:
    """
    表达式重算缓存。

    缓存键：(expr_id, instrument, window_id)。
    window_id：数据窗口的标识（如 "BTC_2024-01-01_2024-06-30"），相同窗口重复求值命中缓存。
    适合因子挖掘期大量重复重算（AlphaGen/GP 评估同一表达式于不同候选集）。
    """

    def __init__(self, max_entries: int = 4096):
        self._cache: dict[tuple[str, str, str], np.ndarray] = {}
        self._max = max_entries
        self.hits = 0
        self.misses = 0

    def get_or_eval(
        self,
        expr: FactorExpr,
        fields: dict[str, np.ndarray],
        instrument: str,
        window_id: str,
    ) -> np.ndarray:
        key = (expr.expr_id, instrument, window_id)
        if key in self._cache:
            self.hits += 1
            return self._cache[key]
        self.misses += 1
        result = expr.evaluate(fields)
        # LRU 式驱逐
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
        return {
            "entries": len(self._cache),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / total if total else 0.0,
        }


# 模块级单例（因子引擎共享）
_default_cache = ExpressionCache()


def get_default_cache() -> ExpressionCache:
    return _default_cache
