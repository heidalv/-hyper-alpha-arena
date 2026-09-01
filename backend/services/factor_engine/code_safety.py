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


def _attr_root_ok(node) -> bool:
    """属性链根是否安全。

    [2026-09-01 F30] 除原白名单外，允许**小写标识符**作为链根：本地 14B 模型
    天然写法是 `x = data['close'].pct_change(5); x.rolling(3).mean()`，旧白名单
    只认 data/np/pd/self 等 → 中间变量方法链全拒（实测 ai_gen 候选 100% 被拒）。
    安全性论证：因子方法体禁止 import（Import 节点直接拒），模板头只注入
    pandas/numpy 与 data/self，方法体内可达对象只剩这些白名单对象与 LLM 自己的
    中间 Series/DataFrame —— 小写根没有可达的危险对象；真正的防线（禁 import、
    禁 dunder、内建函数白名单）全部保留。大写根（潜在类/模块走私）仍拒。
    """
    root = None
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        root = node.id
    if root is None:
        return True
    if root in _SAFE_ATTR_ROOTS:
        return True
    if root.islower() and root.isidentifier():
        return True
    return False


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
            if not _attr_root_ok(node):
                return False, f"属性链根不在白名单: {node.value}."
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in _SAFE_BUILTINS:
                    return False, f"全局函数不在白名单: {node.func.id}()"
            elif isinstance(node.func, ast.Attribute):
                if not _attr_root_ok(node.func):
                    return False, f"方法调用根不在白名单: {node.func}."
        if isinstance(node, ast.Name):
            if node.id.startswith("__"):
                return False, f"禁止 dunder 命名: {node.id}"
    return True, ""
