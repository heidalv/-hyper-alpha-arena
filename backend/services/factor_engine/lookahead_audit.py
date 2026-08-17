"""P1-10 因子源码前视审计（AST 级）。

正则只拦字面 .shift(-数字)；变量负移（如 .shift(-confirm_bars+1)）可绕过。
本模块用 AST 提取 shift 调用参数：
  - 第一参数含一元负号（USub）或负常数 → blocked（引未来数据，禁止加载/评分）
  - shift 参数为非常数表达式（变量/运算）→ review（无法静态判定符号，标记人工复核）
  - 其余 → ok
"""

import ast
import logging
import re

logger = logging.getLogger(__name__)

_LITERAL_NEG_SHIFT_RE = re.compile(r"\.shift\(\s*-\s*\d+")


def _first_arg_has_negative(node: ast.expr) -> bool:
    """shift 第一参数子树内是否含一元负号或负常数。"""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value < 0:
        return True
    if isinstance(node, ast.BinOp):
        return _first_arg_has_negative(node.left) or _first_arg_has_negative(node.right)
    return False


def audit_lookahead(src: str) -> tuple[str, str]:
    """审计因子源码 → ("blocked"|"review"|"ok", detail)。"""
    if not src:
        return "ok", ""
    if _LITERAL_NEG_SHIFT_RE.search(src):
        return "blocked", "literal_shift_negative"
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return "ok", ""  # 语法损坏由 compile 预筛另行处理
    reviewed = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "shift":
            if not node.args:
                continue
            _a = node.args[0]
            if _first_arg_has_negative(_a):
                return "blocked", "ast_shift_negative"
            if not isinstance(_a, ast.Constant):
                reviewed.append(ast.get_source_segment(src, _a) or "?")
    if reviewed:
        return "review", "variable_shift:" + ",".join(reviewed[:3])
    return "ok", ""


def is_lookahead_source(src: str) -> bool:
    """兼容旧接口：blocked 返回 True。"""
    verdict, _ = audit_lookahead(src)
    return verdict == "blocked"