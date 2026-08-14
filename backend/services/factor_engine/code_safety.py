"""code_safety — 因子代码安全校验（阶段4，2026-08-14）。

供 factor_sync_service（云端因子）与 ai_factor_discovery_service（LLM 生成因子）
共用：把黑名单字面量匹配升级为 **AST 白名单**——

允许：
- 无 import（因子代码不允许引入任何模块）
- 属性访问链的根只能是安全白名单（np/pd/df/data/self/result/series 等）
- 禁止 dunder 属性（__class__/__globals__ 等逃逸手段）
- 全局函数调用只允许安全内建白名单（len/abs/min/max/round/sum/float/int/...）
- 禁止 dunder 命名

黑名单（os.system/subprocess/eval/exec/...）仍保留为第一道快速拦截。
"""
from __future__ import annotations

import ast
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# 属性链根白名单：因子代码里允许访问的对象名
_SAFE_ATTR_ROOTS = frozenset({
    "np", "pd", "df", "data", "self", "series", "result",
    "close", "high", "low", "volume", "open", "values", "index",
})

# 全局函数调用白名单（裸名调用）
_SAFE_BUILTINS = frozenset({
    "len", "abs", "min", "max", "round", "sum", "float", "int", "str",
    "bool", "list", "dict", "tuple", "enumerate", "zip", "range", "sorted",
    "isinstance", "any", "all",
})


def _attr_root(node) -> str | None:
    """取属性链的根 Name（df.rolling → 'df'；a.b.c → 'a'）。"""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def ast_whitelist_check(code: str) -> Tuple[bool, str]:
    """AST 白名单校验。返回 (ok, reason)。"""
    try:
        tree = ast.parse(code or "")
    except SyntaxError as e:
        return False, f"语法错误: {e}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "禁止 import"
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                return False, f"禁止 dunder 属性访问: .{node.attr}"
            root = _attr_root(node)
            if root is not None and root not in _SAFE_ATTR_ROOTS:
                return False, f"属性链根不在白名单: {root}."
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in _SAFE_BUILTINS:
                    return False, f"全局函数不在白名单: {node.func.id}()"
            elif isinstance(node.func, ast.Attribute):
                root = _attr_root(node.func)
                if root is not None and root not in _SAFE_ATTR_ROOTS:
                    return False, f"方法调用根不在白名单: {root}."
        if isinstance(node, ast.Name):
            if node.id.startswith("__"):
                return False, f"禁止 dunder 命名: {node.id}"
    return True, ""
