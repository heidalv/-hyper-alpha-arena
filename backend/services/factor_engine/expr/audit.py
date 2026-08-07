"""
因子表达式 DSL — 编译期审计（P1.1）。

目标（方案 R4 / §2.1）：
    在表达式进入候选池之前，静态拦截三类问题：
      1. 结构错误：未知 op / 字段、arity 不匹配、节点类型非法。
      2. Look-ahead bias：负窗口/负延迟（t 时刻用 >t 的数据）。
      3. 安全：AST 只允许 op/field/const/delta_time 节点，无任意代码。

这是因子"无尾巴"纪律的第一道闸门——损坏/未来函数的表达式在 DRAFT 阶段即 REJECTED。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.services.factor_engine.expr.ops import ALLOWED_FIELDS, OP_REGISTRY


@dataclass(frozen=True)
class AuditResult:
    ok: bool
    errors: tuple[str, ...] = ()


def _walk(node: Any, errors: list[str], path: str = "root"):
    """递归校验 AST 节点结构。"""
    if not isinstance(node, dict):
        errors.append(f"{path}: 节点必须是 dict，得到 {type(node).__name__}")
        return

    # 节点类型分发
    if "op" in node:
        op_name = node["op"]
        if op_name not in OP_REGISTRY:
            errors.append(f"{path}: 未知算子 '{op_name}'（不在 OP_REGISTRY）")
            return
        expected_arity, _ = OP_REGISTRY[op_name]
        args = node.get("args", [])
        if not isinstance(args, list):
            errors.append(f"{path}: op '{op_name}' 的 args 必须是 list")
            return
        # 检查 look-ahead：window/d 参数若为常量必须 >= 0
        _check_lookahead(op_name, args, errors, path)
        # 递归校验子节点
        for i, child in enumerate(args):
            if isinstance(child, dict):
                _walk(child, errors, f"{path}.args[{i}]")
            elif isinstance(child, (int, float, str)):
                # 常量参数（窗口/系数）合法，无需进一步校验
                pass
            else:
                errors.append(f"{path}.args[{i}]: 非法参数类型 {type(child).__name__}")
        # arity 校验（仅对纯 op 子节点计数；常量参数算入）
        actual_arity = len(args)
        if actual_arity != expected_arity:
            errors.append(
                f"{path}: op '{op_name}' 期望 {expected_arity} 个参数，得到 {actual_arity}"
            )
    elif "f" in node:
        field_name = node["f"]
        if field_name not in ALLOWED_FIELDS:
            errors.append(f"{path}: 未知字段 '{field_name}'（不在 ALLOWED_FIELDS）")
    elif "c" in node:
        # 常量节点：必须为数值
        if not isinstance(node["c"], (int, float)):
            errors.append(f"{path}: 常量节点 c 必须是数值，得到 {type(node['c']).__name__}")
    elif "d" in node:
        # DeltaTime 节点：时间窗口标记（如 "5m"/"1h"/"1d"），仅校验为字符串
        if not isinstance(node["d"], str):
            errors.append(f"{path}: DeltaTime 节点 d 必须是字符串")
    else:
        errors.append(f"{path}: 无法识别的节点类型，键={list(node.keys())}")


def _check_lookahead(op_name: str, args: list, errors: list[str], path: str):
    """检测 look-ahead bias：滚动窗口/延迟的负值 = 用未来数据。

    仅对窗口/延迟类算子检查负参数（ref/mean/std/rank/delta/decay 等）。
    算术算子（mul/add/sub/pow/greater/less）的负常量完全合法（如 mul(-1, x) 取负）。
    """
    # 只有这些算子的参数中有窗口/延迟语义，负值才是 look-ahead
    WINDOW_OPS = frozenset({
        "ref", "mean", "sum", "std", "var", "max", "min", "rank", "ts_rank",
        "delta", "wma", "ema", "decay_linear", "ts_argmax", "ts_argmin",
        "ts_corr", "corr", "cov", "scale",
    })
    if op_name not in WINDOW_OPS:
        return  # 算术算子不检查负参数

    for i, arg in enumerate(args):
        val = None
        if isinstance(arg, (int, float)):
            val = arg
        elif isinstance(arg, dict) and "c" in arg and isinstance(arg["c"], (int, float)):
            val = arg["c"]
        if val is not None and val < 0:
            errors.append(
                f"{path}: op '{op_name}' 参数 {i}={val} 为负，疑似 look-ahead bias（未来信息）"
            )


def audit(expr_ast: dict) -> AuditResult:
    """
    编译期审计表达式 AST。

    参数：
        expr_ast: 表达式 JSON AST（dict），形如
            {"op":"rank", "args":[{"op":"corr", "args":[{"f":"vwap"},{"f":"volume"},{"c":5}]}]}
    返回：
        AuditResult(ok=True) 通过；否则 errors 列出所有问题。
    """
    errors: list[str] = []
    _walk(expr_ast, errors, "root")
    return AuditResult(ok=len(errors) == 0, errors=tuple(errors))


def is_safe(expr_ast: dict) -> bool:
    """便捷谓词：表达式是否通过审计（无结构错误、无 look-ahead）。"""
    return audit(expr_ast).ok
