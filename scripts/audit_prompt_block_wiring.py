# -*- coding: utf-8 -*-
"""[F369 2026-09-18] 主控 prompt 块接线审计：**建了块但没拼进 prompt** 的静默失效。

由来：与 F367 同类——"生产端有、消费端没接上"。在 prompt 装配这一层，
典型形态是某个 `_build_xxx_block()` 被调用但返回值被丢弃，或干脆没人调用，
于是那块内容**永远不进 LLM**（本次复查已实证两例：`{factor_guidance}` 渲染成 "N/A"、
FUSION VERDICTS 的读取点只在预览 API 里）。

方法（AST + 调用点上下文，只读）：
  1. 找出模块内所有"块构造器"（名字含 `_block` 的方法 / 返回 str 的 `_build_*`）；
  2. 统计其调用点，并打印调用行**上下文**，以区分：
       - `lines.append(self._build_x())` / `parts += ...` → 已拼进 prompt ✓
       - `self._build_x()` 裸调用（返回值丢弃） → 建了没用 ✗
       - 0 个调用点 → 死代码 ✗
"""
from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TARGETS = [
    "backend/services/trading_analysts.py",
    "backend/services/ai_decision_service.py",
    "backend/services/mlto/brain.py",
]

#: 结果被拼进 prompt 的常见宿主方法名（出现这些即视为"已接"）
ASSEMBLY_HINTS = ("append", "extend", "join", "+=", "+ self", "return self", "parts", "lines")


def block_builders(tree: ast.AST, src: str) -> list:
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if "_block" not in name and not name.startswith("_build_"):
            continue
        seg = ast.get_source_segment(src, node) or ""
        # 只关心"返回文本"的块构造器
        if "-> str" in seg[:200] or re.search(r"\breturn\b", seg):
            out.append((name, node.lineno))
    return out


def _parents(tree: ast.AST) -> dict:
    """构建 child -> parent 映射（用于判断调用处于什么语法位置）。"""
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[id(child)] = node
    return out


def _used_later(tree: ast.AST, var: str, after_line: int) -> bool:
    """同一函数体内，`after_line` 之后是否有读取该变量的地方。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == var and isinstance(node.ctx, ast.Load):
            if node.lineno > after_line:
                return True
    return False


def analyse_calls(tree: ast.AST, src: str, name: str) -> list:
    """返回 [(行号, 调用行, 判定, 证据)]。

    判定只看**语法位置**，不看名字后不后续（这是 F369 修正后的正确口径）：
      - 处于 Assign 值位 → 再看该变量在后续是否被读取（Load）；
      - 处于 Expr 语句位（裸调用）→ 返回值丢弃；
      - 处于 Return / JoinedStr / Call 实参 / BinOp / List 等 → 直接被消费 ✓。
    """
    par = _parents(tree)
    lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        fn = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if fn != name:
            continue
        ctx = lines[node.lineno - 1].strip()[:110]
        p = par.get(id(node))
        if isinstance(p, ast.Assign):
            names = [t.id for t in p.targets if isinstance(t, ast.Name)]
            if not names:
                out.append((node.lineno, ctx, "assigned-复杂目标", "人工核"))
                continue
            var = names[0]
            ok = _used_later(tree, var, node.lineno)
            out.append((node.lineno, ctx,
                        "已接(赋值后被读取)" if ok else "**赋值后从未读取 ⇒ 建了没用**",
                        f"{var}"))
        elif isinstance(p, ast.Expr):
            out.append((node.lineno, ctx, "**裸调用 ⇒ 返回值丢弃**（除非函数有副作用）", ""))
        else:
            out.append((node.lineno, ctx, "已接(直接消费)", type(p).__name__))
    return out


def main() -> int:
    print("=" * 96)
    print("主控 / 决策服务 / 主脑 prompt 块接线审计")
    print("=" * 96)
    grand_dead = []
    for rel in TARGETS:
        p = ROOT / rel
        if not p.exists():
            print(f"\n[{rel}] 不存在，跳过")
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        # [F369] 有的文件带 BOM（U+FEFF）⇒ `ast.parse(str)` 会 SyntaxError
        # （importlib 走 utf-8-sig 所以运行没问题，纯属本脚本的解析口径问题）。
        bom = src.startswith("\ufeff")
        src = src.lstrip("\ufeff")
        try:
            tree = ast.parse(src)
        except Exception as exc:
            print(f"\n[{rel}] 解析失败: {exc}")
            continue
        builders = block_builders(tree, src)
        print(f"\n[{rel}]  块构造器 {len(builders)} 个"
              f"{'  （文件带 BOM，已剥离后解析）' if bom else ''}")
        for name, lineno in sorted(builders, key=lambda x: x[1]):
            sites = analyse_calls(tree, src, name)
            if not sites:
                verdict = "**0 调用点（死代码）**"
                grand_dead.append((rel, name, "0 调用"))
            else:
                bad = [s for s in sites if s[2].startswith("**")]
                if bad:
                    verdict = f"**{len(bad)}/{len(sites)} 处疑似未接**"
                    grand_dead.append((rel, name, f"{len(bad)}/{len(sites)} 未接"))
                else:
                    verdict = f"已接（{len(sites)} 处）"
            print(f"   {name:<44} 定义@{lineno:<6} {verdict}")
            for ln, ctx, why, extra in sites[:3]:
                print(f"        :{ln}  [{why}{(' ' + extra) if extra else ''}]  {ctx}")

    print("\n" + "=" * 96)
    print(f"需要人工裁定的项：{len(grand_dead)}")
    for rel, name, why in grand_dead:
        print(f"  ⚠️ {rel.split('/')[-1]}::{name}  （{why}）")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
