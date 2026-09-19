# -*- coding: utf-8 -*-
"""轮117 之四：`_fill_market_order` 的致命 NameError（**我自己 02:44 引入的回归**）。

## 现场（traceback 原文，logs/backend.log 16:49:46）

```
File "backend/services/paper_trading_engine.py", line 1771, in _fill_market_order
    order.tp_price, side=str(side or ""), market=float(current_price or 0),
NameError: name 'side' is not defined
⇒ [FullAuto] 模拟交易执行异常 → place_order 返回 False → 审计 paper_trade_false
```

`_fill_market_order(self, db, order, bal, timeframe_tier, add_type, trade_nature, …)`
**没有 `side` / `symbol` 形参**。轮104 续加的"取价后复验 TP"用了这两个名字 ⇒
**只要 `order.tp_price` 非空就炸**：
* 中线每单都带 TP ⇒ **全军覆没**（"中线被冻结"的终端根因）；
* 长线 trend_e1 订单 TP 为空 ⇒ 恰好绕过（所以"只有中线被冻、长线正常"）；
* 时间线吻合：轮104 续 02:44 提交，中线最后一笔成交停在 **02:28**。

本文件用**未定义名分析**（pyflakes-lite）把这个函数钉住：任何 `Name` 读取必须在
（形参 ∪ 局部赋值 ∪ 模块全局 ∪ 内建）里；这一类 bug 不会再静默通过。
"""
import ast
import builtins
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_PTE = os.path.join(_ROOT, "backend/services/paper_trading_engine.py")


def _fn_node(src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_fill_market_order":
            return node
    raise AssertionError("未找到 _fill_market_order")


def _bound_names(fn, module_globals):
    """形参 ∪ 局部赋值 ∪ 模块全局/导入 ∪ 内建。"""
    bound = set(module_globals)
    bound |= set(dir(builtins))
    for a in list(fn.args.args) + list(fn.args.kwonlyargs) + list(fn.args.posonlyargs):
        bound.add(a.arg)
    if fn.args.vararg:
        bound.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        bound.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                bound.add((al.asname or al.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    return bound


def _loaded_names(fn):
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            out.append((node.id, node.lineno))
    return out


def test_no_undefined_names_in_fill_market_order():
    src = io.open(_PTE, encoding="utf-8").read()
    tree = ast.parse(src)
    module_globals = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    module_globals.add(t.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                module_globals.add((al.asname or al.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            module_globals.add(node.name)
        elif isinstance(node, ast.Try):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for al in sub.names:
                        module_globals.add((al.asname or al.name).split(".")[0])
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            module_globals.add(t.id)
    fn = _fn_node(src)
    bound = _bound_names(fn, module_globals)
    bad = [(n, ln) for n, ln in _loaded_names(fn) if n not in bound]
    assert not bad, f"_fill_market_order 里有未定义名（会在运行时 NameError）: {bad}"


def test_tp_recheck_uses_order_attributes_not_free_vars():
    src = io.open(_PTE, encoding="utf-8").read()
    i = src.index("轮117 2026-09-19 修 **致命 NameError**")
    seg = src[i:i + 1800]
    # 注释里必然引用旧代码作为证据 ⇒ 只检查**可执行文本**
    code = "\n".join(l for l in seg.splitlines() if not l.strip().startswith("#"))
    assert "side=_side" in code and '_side = str(getattr(order, "side"' in code
    assert 'side=str(side or "")' not in code, "旧的自由变量 side 又回来了"
    assert "\n                    symbol, side, order.tp_price" not in code
    assert "_sym, _side, order.tp_price" in code


def test_evidence_is_documented_next_to_the_fix():
    src = io.open(_PTE, encoding="utf-8").read()
    i = src.index("轮117 2026-09-19 修 **致命 NameError**")
    seg = src[i:i + 1400]
    assert "paper_trade_false" in seg and "02:28" in seg, \
        "必须留下现场证据（审计落成 paper_trade_false / 中线最后一笔成交 02:28）"
