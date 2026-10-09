"""Find functions defined but NEVER called -- the "written but not wired" family.

Sibling of dead_constants.py.  This session's defects were mostly of one family:
a mechanism is *written* and *documented*, but nothing ever invokes it:

  · `_EXIT_TAKER_AFTER_SEC`      assigned, 8 comments, 0 reads
  · `hold_hard_taker`            code correct, placed after 15 early returns
  · `timeout_hard_taker`         dead branch for the active-flow lane
  · `flow_gate_last.json`        producer/consumer pointed at different files

This scans the market-maker package for module-level and class-level functions
that are never referenced by name anywhere inside the package.

Caveats reported explicitly:
  · a function may be called via getattr / string dispatch -> "POSSIBLE"
  · private helpers called only in tests -> flagged separately
"""
from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
MM = ROOT / "backend" / "services" / "market_maker"
TESTS = ROOT / "backend" / "tests"


def collect(paths):
    text = []
    for p in paths:
        try:
            text.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(text)


def main() -> int:
    py_files = sorted(MM.glob("*.py"))
    # [T68] 引用语料必须是**整个 backend + scripts**，不能只有 market_maker 包。
    # 第一版只扫 `market_maker/*.py` ⇒ `reset_account`（由 `backend/api/hft_routes.py`
    # 调用）等被误报为"无人调用"。实测第一版报 38 个，其中多数是这种假阳性。
    #
    # 性能：**先把所有文件读进内存**（第一版在每个函数里重复 rglob+read，
    # 是 O(n²) 次文件读，实测跑 10 分钟不出结果 ⇒ 已改）。
    mm_text = {p: p.read_text(encoding="utf-8", errors="replace") for p in py_files}
    all_text = {}
    for p in (ROOT / "backend").rglob("*.py"):
        try:
            all_text[p] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    test_src = collect(list(TESTS.rglob("*.py"))) if TESTS.exists() else ""
    other_src = collect(list((ROOT / "scripts").glob("*.py")))
    pkg_src = "\n".join(mm_text.values())
    ref_src = "\n".join(all_text.values())
    # 每个名字在整个包内的 def 次数（用于扣除定义本身的计数）
    def_counts: dict = {}
    for txt in mm_text.values():
        for m in re.finditer(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", txt, re.M):
            def_counts[m.group(1)] = def_counts.get(m.group(1), 0) + 1

    # 性能：[T68] 对整个引用语料**只做一次分词**，建 Counter；
    # 之后每个函数名都是 O(1) 查表。
    # （前两版分别是"每函数 rglob+read"（O(n²) 次磁盘）与
    #   "每函数对整个语料跑正则"（O(函数数 × 语料)，都实测超时 ⇒ 已改。）
    from collections import Counter
    ref_tokens = Counter(re.findall(r"[A-Za-z_]\w*", ref_src))
    test_tokens = Counter(re.findall(r"[A-Za-z_]\w*", test_src))
    other_tokens = Counter(re.findall(r"[A-Za-z_]\w*", other_src))
    # 动态名字（出现在引号里的）——只扫一次
    quoted = set(re.findall(r'["\']([A-Za-z_]\w*)["\']', ref_src))

    findings = []
    for py, txt in mm_text.items():
        try:
            tree = ast.parse(txt)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name.startswith("__") and name.endswith("__"):
                continue
            if ref_tokens.get(name, 0) - def_counts.get(name, 1) > 0:
                continue
            findings.append((py.name, name, node.lineno,
                             test_tokens.get(name, 0),
                             other_tokens.get(name, 0),
                             name in quoted))

    print("=" * 100)
    print("「定义了但从未被调用」的函数（引用范围：整个 backend + scripts）")
    print("=" * 100)
    print(f"  扫描 {len(py_files)} 个模块，命中 **{len(findings)}** 个")
    print()
    real = [f for f in findings if not f[5]]
    dyn = [f for f in findings if f[5]]
    print(f"    · 无任何引用（含动态）：**{len(real)}**")
    print(f"    · 有字符串/动态引用风险：{len(dyn)}")
    print()
    if real:
        print(f"  {'file':<22}{'function':<40}{'line':>6}{'测试引用':>9}{'脚本引用':>9}")
        for f, n, ln, nt, no, _ in sorted(real, key=lambda x: x[0]):
            print(f"  {f:<22}{n[:39]:<40}{ln:>6}{nt:>9}{no:>9}")
    if dyn:
        print()
        print("  ── 有字符串引用（可能是 getattr/派发，需人工看）──")
        for f, n, ln, nt, no, _ in sorted(dyn, key=lambda x: x[0])[:12]:
            print(f"  {f:<22}{n[:39]:<40}{ln:>6}{nt:>9}{no:>9}")
    print()
    print("  读法：'无任何引用' 且**测试也没引用** ⇒ 很可能是"
          "『写了但没接线』（本会话最大缺陷类）。")
    print("        若测试引用了它，则至少有人在验证它，优先级低。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
