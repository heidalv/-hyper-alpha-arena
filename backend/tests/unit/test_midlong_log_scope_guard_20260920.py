# -*- coding: utf-8 -*-
"""[轮132 2026-09-20] 日志作用域守卫：**再不让"打日志用了作用域外的名字"冻结车道**。

## 事故（本轮实测，与 轮117 同类）
`midlong_executor.apply_regime_to_open()` 的三处日志写 `tier=%s`，但**形参里没有 `tier`**
⇒ 只要走到 ranging 分支（当前最常见的 regime）就抛
`NameError: name 'tier' is not defined`，被 `maybe_open` 兜住记成「开仓失败」——
**11:47 之后 24 次开仓全部失败、中线零成交**。同一函数里还有一条日志
**6 个 `%s` 只给了 5 个实参**（缺 `tier`），logging 内部报错、输出被打断。
根因：轮124/125 批量给 `[MidLong]` 日志补 `tier=%s` 时，没有校验"这个名字在该函数里是否在作用域内"。

## 本文件守两件事（全库扫描，不是只测一个函数）
1. **占位符数量 == 实参数量**（`%s/%d/%.1f` 等一并对齐）——抓"6 vs 5"这类静默日志错误；
2. **被当实参使用的裸名字必须在函数作用域内**（形参 / 赋值 / 循环变量 / except as）——
   抓"`tier` 不在作用域"这类会**抛异常**的错误（比日志错误更致命）。
外加一条**运行时**断言：把 `apply_regime_to_open` 的四个 regime 分支都真的跑一遍。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SCAN_DIRS = ("backend/services/full_auto", "backend/services/mlto", "backend/services/analysts")
_PLACEHOLDER = re.compile(r"%(?:\d+\$)?[-+ #0]*\d*(?:\.\d+)?[sdrfgeuxX]")


def _iter_log_calls(path: Path):
    """产出 (函数名, 行号, 格式串, 位置实参节点列表, 作用域内可用名字)。

    [轮132 精确化] 首版只统计"裸名字实参"并且只看**单个函数体** ⇒ 两类误报：
      · 模块级常量（如 `TABLE`）被当成"不在作用域"；
      · 闭包变量（嵌套函数引用外层局部变量，如 `_bg()` 用外层 `tier`）被误判。
    现在：位置实参**全量计数**（占位符校验用），作用域名字集合 = 本函数 + 所有外层函数 + 模块级。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    module_names = _module_bound_names(tree)

    def walk_scope(node: ast.AST, inherited: set, fname: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner = set(inherited)
                inner |= _local_bound_names(child)
                yield from walk_scope(child, inner, child.name)
            elif isinstance(child, (ast.ClassDef, ast.If, ast.Try, ast.For, ast.While, ast.With)):
                yield from walk_scope(child, inherited, fname)
            elif isinstance(child, ast.Call):
                f = child.func
                name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if (name in ("info", "warning", "error", "debug", "critical", "exception")
                        and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                        and f.value.id == "logger" and child.args
                        and isinstance(child.args[0], ast.Constant)
                        and isinstance(child.args[0].value, str)):
                    yield (fname, child.lineno, child.args[0].value,
                           list(child.args[1:]), set(inherited) | module_names)

    for top in tree.body:
        yield from walk_scope(top, set(), "<module>")


def _module_bound_names(tree: ast.AST) -> set:
    out: set = {"logger", "self", "cls", "__name__"}
    for node in tree.body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                out.add(sub.id)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for a in sub.names:
                    out.add((a.asname or a.name).split(".")[0])
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(sub.name)
    return out


def _local_bound_names(fn: ast.AST) -> set:
    """本函数内绑定的名字（形参 + 赋值 + for/with/except/import/global）。"""
    bound: set = {"self", "cls", "logger"}
    args = getattr(fn, "args", None)
    if args is not None:
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            bound.add(a.arg)
        if args.vararg:
            bound.add(args.vararg.arg)
        if args.kwarg:
            bound.add(args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.For):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    bound.add(t.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.comprehension):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    bound.add(t.id)
        elif isinstance(node, ast.Lambda):
            for a in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
                bound.add(a.arg)
        elif isinstance(node, ast.Global):
            bound.update(node.names)
    return bound


def test_placeholder_count_matches_args_everywhere():
    """`%s` 个数必须等于**位置实参个数**（有 `*args` 展开时跳过）。"""
    bad = []
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            for fname, lineno, fmt, arg_nodes, _scope in _iter_log_calls(p):
                if any(isinstance(a, ast.Starred) for a in arg_nodes):
                    continue
                n_ph = len(_PLACEHOLDER.findall(fmt))
                # %% 是字面百分号，不计入占位符
                n_ph -= fmt.count("%%")
                if n_ph != len(arg_nodes):
                    bad.append(f"{p.relative_to(ROOT).as_posix()}:{lineno} in {fname}() "
                               f"占位符={n_ph} 实参={len(arg_nodes)} :: {fmt[:70]}")
    assert not bad, "日志占位符与位置实参数量不一致（logging 内部报错、输出被打断）：\n" + "\n".join(bad[:15])


def test_log_arg_names_are_in_scope():
    """裸名字实参必须在**本函数 / 外层函数 / 模块级**任一作用域内。"""
    bad = []
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            for fname, lineno, fmt, arg_nodes, scope in _iter_log_calls(p):
                for a in arg_nodes:
                    if isinstance(a, ast.Name) and a.id not in scope:
                        bad.append(f"{p.relative_to(ROOT).as_posix()}:{lineno} in {fname}() "
                                   f"实参 `{a.id}` 不在作用域 :: {fmt[:60]}")
    assert not bad, ("日志实参引用作用域外的名字（NameError ⇒ 业务中断）：\n" + "\n".join(bad[:15]))


def test_tier_tagged_logs_have_tier_in_scope():
    """[轮124 遗留风险] 打了 `tier=%s` 的日志，作用域里必须有 `tier`。"""
    bad = []
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            for fname, lineno, fmt, _args, scope in _iter_log_calls(p):
                if "tier=%s" in fmt and "tier" not in scope:
                    bad.append(f"{p.relative_to(ROOT).as_posix()}:{lineno} in {fname}()")
    assert not bad, "这些日志打了 tier 但作用域里没有 tier：\n" + "\n".join(bad[:15])


# ───────────────── 运行时：四个 regime 分支都要真的能跑 ─────────────────

@pytest.mark.parametrize("ms,expect_regime", [
    ({"BTC": {"regime": "trend"}}, "trend"),
    ({"BTC": {"regime": "ranging"}}, "ranging"),
    ({"BTC": {"regime": "unknown"}}, "unknown"),
    ({"BTC": {"regime": "extreme"}}, "extreme"),
])
def test_apply_regime_to_open_all_branches_run(ms, expect_regime, monkeypatch):
    """回归：每个 regime 分支都必须能跑通（ranging 分支过去必抛 NameError）。"""
    monkeypatch.setenv("MIDLONG_ALLOW_RANGE_PROBE", "true")
    from backend.services.full_auto.midlong_executor import apply_regime_to_open

    act, margin, regime, reason = apply_regime_to_open(
        symbol="BTC", action="buy", market_summary=ms, trading_mode="paper",
        tranche_margin_pct=1.0, tier="mid",
    )
    assert isinstance(act, str) and isinstance(reason, str)
    assert 0.0 <= float(margin) <= 1.0
    # regime 由 classify_regime 判定（此处只要求不抛 + 返回值形态正确）


def test_apply_regime_to_open_without_tier_does_not_crash(monkeypatch):
    """不传 tier 也必须安全（默认空串）—— 防"少传一个 kwarg 就全挂"。"""
    monkeypatch.setenv("MIDLONG_ALLOW_RANGE_PROBE", "true")
    from backend.services.full_auto.midlong_executor import apply_regime_to_open

    act, margin, regime, reason = apply_regime_to_open(
        symbol="BTC", action="buy", market_summary={"BTC": {"regime": "ranging"}},
        trading_mode="paper", tranche_margin_pct=1.0,
    )
    assert isinstance(act, str) and isinstance(reason, str)
