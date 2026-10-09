"""h543：给判定框架补上**字典型参数**支持（h527 的前置）。

背景：`h527` 的逐币旋钮值是 `{"NEAR": 0.35, ...}` 这类字典，而
`h425_repair_trial.py` 的 `do_deploy/do_rollback/do_judge` 原先一律 `float(t)`：
遇字典直接 `TypeError` ⇒ **框架无法部署逐币参数** ⇒ 只能手写登记表，
那等于绕过「预注册 → 试跑 → 判定 → 自动回滚」整条治理链。

本补丁是**纯机械替换**（幂等，可重复运行）：
  · `float(t), float(rb)`            → `_coerce_param(t), _coerce_param(rb)`   （3 处）
  · `float(params.get(...) or 0.0)`  → `_current_of(params, ...)`              （3 处）
助手函数 `_coerce_param` / `_current_of` 已加在同文件（只放宽字典，其余仍强制 float）。

用法：python scripts/h543_patch_trial_dict_params.py [--check]
"""
from __future__ import annotations

import ast
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "h425_repair_trial.py"

PAIRS = [
    ('fields = [(f.split(".")[-1], float(t), float(rb))',
     'fields = [(f.split(".")[-1], _coerce_param(t), _coerce_param(rb))'),
    ("old = {name: float(params.get(name) or 0.0) for name, *_ in fields}",
     "old = {name: _current_of(params, name, 0.0) for name, *_ in fields}"),
    ("old = {n: float(params.get(n) or 0.0) for n, *_ in fields}",
     "old = {n: _current_of(params, n, 0.0) for n, *_ in fields}"),
]


def main() -> int:
    check = "--check" in sys.argv
    s = TARGET.read_text(encoding="utf-8")
    if "_coerce_param" not in s:
        print("✗ 未找到 `_coerce_param`（助手函数未加？先加助手再跑本补丁）")
        return 1
    total = 0
    for old, new in PAIRS:
        n = s.count(old)
        if n:
            s = s.replace(old, new)
            total += n
            print(f"  ✓ 替换 {n} 处：{old[:52]}…")
        else:
            print(f"  · 已是最新（0 处）：{old[:52]}…")
    left = s.count("float(t), float(rb)") + s.count("float(params.get(")
    print(f"\n替换合计 {total} 处；残留旧写法 {left} 处"
          f"{'（应为 0）' if left == 0 else ' ✗ 请检查'}")
    if check:
        print("（--check：不写入）")
        return 0 if left == 0 else 1
    ast.parse(s)                      # 写前先验证语法
    TARGET.write_text(s, encoding="utf-8")
    print(f"✓ 已写入 {TARGET.relative_to(ROOT)}（AST 校验通过）")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
