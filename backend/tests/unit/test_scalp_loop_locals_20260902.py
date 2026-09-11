"""scalp_loop 局部变量"先赋值后引用"静态检查。

[2026-09-02] 背景
-----------------
`_run_scalp_independent_inner` 是 2000+ 行的大函数。`_trade_mode` 原先在
ScalpExecutionGate 处才赋值，但紧靠其上的 pwin 仲裁块已经引用它，触发
UnboundLocalError：

- 旧版仲裁异常 fail-open：每 tick **第一个** buy/sell 信号抛异常被静默放行、
  绕过 pwin 闸门；之后 `_trade_mode` 被下方赋值，后续币才正常仲裁。
  即"唯一经数据验证有效的质量闸"一直有一个隐蔽漏洞。
- G18 改 fail-closed 后，第一个信号 continue 掉、永远到不了赋值行 →
  17:47 起 2970 次 100% 拒开，短线 5 小时零成交。

这类 bug 在大函数里靠人眼看不出、靠 mock 全链路的单测也很难触发（需要构造
真实的 buy/sell 信号一路走到仲裁）。用 AST 直接检查"该名字首次 Store 的行号
必须早于首次 Load 的行号"最便宜、最可靠。

约束仅覆盖那些**在函数体内先赋值再被多处引用**的运行期状态变量；不做全量
未绑定分析（那是 pyflakes 的活，且对分支赋值会大量误报）。
"""
from __future__ import annotations

import ast
import os

import pytest

_SCALP_LOOP = os.path.join(
    os.path.dirname(__file__), "..", "..", "services", "full_auto", "loops", "scalp_loop.py",
)
_FUNC = "_run_scalp_independent_inner"

# 必须"先赋值后引用"的局部名。加名字前请确认它确实是函数内先赋值的运行期状态，
# 而不是循环变量 / 仅在某分支内使用的临时量。
_MUST_BIND_BEFORE_USE = ("_trade_mode",)


def _load_func() -> ast.FunctionDef:
    with open(_SCALP_LOOP, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=_SCALP_LOOP)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == _FUNC:
            return node
    pytest.fail(f"未找到函数 {_FUNC}，文件结构可能已变，请更新本测试")


def _first_store_and_load(func: ast.FunctionDef, name: str):
    """返回 (首次赋值行号, 首次引用行号)；None 表示未出现。

    只看 `func` 自身作用域：嵌套 def/lambda 有独立作用域，其内对同名的 Load 是闭包
    引用，运行期在调用时才解析，不属于本检查范围。
    """
    first_store = None
    first_load = None

    class _V(ast.NodeVisitor):
        def visit_FunctionDef(self, node):  # 不进入嵌套作用域
            if node is func:
                self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Lambda(self, node):
            pass

        def visit_Name(self, node: ast.Name):
            nonlocal first_store, first_load
            if node.id != name:
                return
            if isinstance(node.ctx, ast.Store):
                if first_store is None or node.lineno < first_store:
                    first_store = node.lineno
            elif isinstance(node.ctx, ast.Load):
                if first_load is None or node.lineno < first_load:
                    first_load = node.lineno

    _V().visit(func)
    return first_store, first_load


@pytest.mark.parametrize("name", _MUST_BIND_BEFORE_USE)
def test_local_bound_before_first_use(name: str):
    func = _load_func()
    store, load = _first_store_and_load(func, name)
    assert store is not None, f"{name} 在 {_FUNC} 内从未赋值——若已重构请更新 _MUST_BIND_BEFORE_USE"
    assert load is not None, f"{name} 在 {_FUNC} 内从未被引用——若已重构请更新 _MUST_BIND_BEFORE_USE"
    assert store < load, (
        f"{name} 首次引用在第 {load} 行，但首次赋值在第 {store} 行 —— "
        f"引用早于赋值会触发 UnboundLocalError。pwin 仲裁 fail-closed 下这意味着 100% 拒开。"
    )


def test_trade_mode_bound_before_pwin_arbiter():
    """更具体的锁定：_trade_mode 的赋值必须早于 decide_scalp(...) 调用。"""
    func = _load_func()
    store, _ = _first_store_and_load(func, "_trade_mode")
    arbiter_call_line = None
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
            if fname == "decide_scalp":
                arbiter_call_line = node.lineno if arbiter_call_line is None else min(arbiter_call_line, node.lineno)
    assert arbiter_call_line is not None, "未找到 decide_scalp 调用，pwin 仲裁接线可能已变"
    assert store is not None and store < arbiter_call_line, (
        f"_trade_mode 赋值(第 {store} 行)必须早于 pwin 仲裁 decide_scalp(第 {arbiter_call_line} 行)"
    )
