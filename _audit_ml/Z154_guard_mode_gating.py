# -*- coding: utf-8 -*-
"""Z154 (目标项 (c)): 找出「只在 paper 或只在 live 分支里被调用」的安全闸。

方法：AST 扫描 backend/services/**，对每个疑似安全闸调用（名字命中
gate/guard/allow/deny/block/choke/constitution/cooldown/quota/circuit/veto/limit 等），
检查它的**所有祖先 If 节点**的测试表达式是否含 mode/trading_mode/live/paper 字样。
若某个闸的全部调用点都被 mode 条件包裹 ⇒ 该态下无保护（真分叉）；
若同一闸同时存在「无条件调用点」和「mode 内调用点」⇒ 视为已由无条件路径覆盖。
"""
from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services")

GUARD_RE = re.compile(
    r"(gate|guard|allow|deny|block|choke|constitution|cooldown|quota|circuit|veto|"
    r"kill_switch|limit_ok|check_.*(open|close|trade|risk|exposure)|assert_live)",
    re.I,
)
MODE_RE = re.compile(r"(trade_)?mode|trading_mode|\blive\b|\bpaper\b", re.I)

# 与中长线无关的模块前缀（纯技术性/噪声）
SKIP_RE = re.compile(r"(tests?/|__pycache__)", re.I)


class V(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.stack: list[ast.If] = []
        self.calls: list[tuple[str, int, str]] = []  # (name, lineno, enclosing mode-test)

    def visit_If(self, node: ast.If) -> None:
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name and GUARD_RE.search(name):
            modes = []
            for anc in self.stack:
                t = ast.unparse(anc.test)
                if MODE_RE.search(t):
                    modes.append(t[:90])
            self.calls.append((name, node.lineno, " && ".join(modes)))
        self.generic_visit(node)


def main() -> None:
    per_guard: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for py in ROOT.rglob("*.py"):
        rel = str(py.relative_to(ROOT))
        if SKIP_RE.search(rel):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        v = V(py)
        v.visit(tree)
        for name, line, mode_test in v.calls:
            per_guard[name].append((rel, line, mode_test))

    mode_only, mixed, unconditional = [], [], []
    for name, sites in sorted(per_guard.items()):
        gated = [s for s in sites if s[2]]
        free = [s for s in sites if not s[2]]
        if gated and not free:
            mode_only.append((name, gated))
        elif gated and free:
            mixed.append((name, gated, free))
        else:
            unconditional.append((name, sites))

    print(f"扫描到疑似安全闸调用名 {len(per_guard)} 个")
    print(f"\n=== A. 全部调用点都在 mode 条件内（需人工判定是否真分叉）: {len(mode_only)} ===")
    for name, gated in mode_only:
        print(f"\n  {name}  ({len(gated)} 处)")
        for rel, line, t in gated[:6]:
            print(f"    {rel}:{line}  <= if {t}")
    print(f"\n=== B. 部分在 mode 内、部分无条件（视为已由无条件路径覆盖）: {len(mixed)} ===")
    for name, gated, free in mixed:
        print(f"  {name}: mode 内 {len(gated)} 处 / 无条件 {len(free)} 处"
              f"  | 例: {gated[0][0]}:{gated[0][1]} if {gated[0][2][:60]}")
    print(f"\n=== C. 与 mode 无关（无条件调用）: {len(unconditional)} ===")


if __name__ == "__main__":
    main()
