# -*- coding: utf-8 -*-
"""[F335 2026-09-22] 校验 `exit_reason` 落盘字段。

# 为什么需要独立的校验脚本

`meta["exit_reason"] = str(getattr(decision, "skip", "") or "")` 这行依赖
`decision` 在**写入点**（`lane_ledger.record_fill(...)` 处）作用域内。
若它不在，Python 会抛 `NameError` —— 而这段代码整体包在
`try/except Exception` 里（`logger.warning("[F60] record_fill 失败: %s")`），
**异常会被吞成一条 warning，字段静默为空**。

那正是本仓库反复踩的"改了但没生效"（F189/F280/F287/F292/F327）。

⇒ 用 AST 静态确认：
  1. 每个含 `lane_ledger.record_fill(` 的函数里，`decision` 都是局部名或参数
  2. 该函数段里确实引用了 `exit_reason`
  3. 写入的 meta 字典里包含 `exit_reason` 键（不是只出现在注释里）
"""
from __future__ import annotations

import ast
import io
import pathlib
import sys

# 本文件在 backend/tests/unit/ ⇒ parents[3] = 仓库根
#   parents[0]=unit  [1]=tests  [2]=backend  [3]=Hyper-Alpha-Arena
ROOT = pathlib.Path(__file__).resolve().parents[3]
RUNNER = ROOT / "backend" / "services" / "market_maker" / "runner.py"


def main() -> int:
    src = io.open(RUNNER, encoding="utf-8").read()
    tree = ast.parse(src)

    found = 0
    bad = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if "lane_ledger.record_fill(" not in seg:
            continue
        found += 1
        local = {n.id for n in ast.walk(node)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        args = {a.arg for a in node.args.args}
        has_decision = "decision" in (local | args)
        has_reason = "exit_reason" in seg

        print(f"  函数 {node.name}  (line {node.lineno})")
        print(f"    decision 在作用域内 : {has_decision}")
        print(f"    引用了 exit_reason  : {has_reason}")

        if not has_decision:
            print("    ✗ decision 不在作用域内 ⇒ 会抛 NameError 并被 except 吞掉")
            bad += 1
        if not has_reason:
            print("    ✗ 未写入 exit_reason")
            bad += 1

        # 确认它出现在 meta 字典的键位（而非仅注释）—— 检查剥离注释后的文本
        code_only = "\n".join(
            ln.split("#", 1)[0] for ln in seg.splitlines())
        if '"exit_reason"' not in code_only:
            print('    ✗ 可执行代码里没有 "exit_reason" 字面量（只出现在注释里？）')
            bad += 1

    print()
    if found == 0:
        print("  ✗ 找不到任何含 lane_ledger.record_fill( 的函数")
        return 1
    if bad:
        print(f"  ✗ {bad} 项校验失败")
        return 1
    print(f"  ✓ {found} 处写入点全部通过：decision 在作用域内，exit_reason 会真的落盘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
