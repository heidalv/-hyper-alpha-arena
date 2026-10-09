"""h552：打印判定产物的**实际字段结构**，用于校准验收助手 `h546` 的字段映射。

背景：`h546` 的"预注册预期 → 实测值"取值器是手写的；若字段名与实际产物不一致，
验收时会打印"(取不到)"——那正是要避免的。本脚本用 AST 静态提取：
  1. `_sub_stats` 各分支实际产出的键；
  2. `do_judge` 里 `res` 的顶层键。

用法：python scripts/h552_verdict_schema.py [--key h464]
"""
from __future__ import annotations

import ast
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRIAL = ROOT / "scripts" / "h425_repair_trial.py"


def main() -> int:
    src = TRIAL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    print("=" * 80)
    print("[1] `_sub_stats` 各分支的产出键")
    print("=" * 80)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_sub_stats":
            cur_keys: list[str] = []
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Name)
                        and sub.left.id == "key"):
                    for c in sub.comparators:
                        if isinstance(c, ast.Constant) and isinstance(c.value, str):
                            cur_keys.append(c.value)
                if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name)
                        and sub.value.id == "out" and isinstance(sub.slice, ast.Constant)
                        and isinstance(sub.slice.value, str)):
                    print(f"  out[{sub.slice.value!r}]")
    print("\n" + "=" * 80)
    print("[2] `do_judge` 里 `res` 的顶层键")
    print("=" * 80)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "do_judge":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Dict):
                    for t in sub.targets:
                        if isinstance(t, ast.Name) and t.id == "res":
                            for k in sub.value.keys:
                                if isinstance(k, ast.Constant):
                                    print(f"  {k.value}")
    print("\n用法：把上面的键与 `h546` 里的取值器逐条对齐即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
