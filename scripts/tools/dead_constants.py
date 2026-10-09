"""Find "declared but never read" constants -- the defect class that cost the most.

This session's single biggest finding was `_EXIT_TAKER_AFTER_SEC`:
assigned once, mentioned in 8 comments, **read by zero code**.  T17 relied on it
as a safety net, so stops silently never forced an exit, and 7.7% of exit legs
ate 97.6% of the loss.

That was found by hand.  This tool finds the whole class automatically:
for every module-level UPPER_CASE name (and leading-underscore constant) in the
market-maker package, count how many times it is read vs merely assigned/mentioned.

Classification:
  READ      -- appears in code other than its own assignment
  ASSIGN-ONLY -- assigned, then only mentioned in comments/strings  <- DEFECT
  UNUSED    -- never appears again at all                            <- DEAD
"""
from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
MM = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\market_maker")
NAME_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")


def strip_comments_and_strings(src: str) -> str:
    """Blank out comments and string literals so mentions don't count as reads."""
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    lines = src.splitlines()
    kill = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                kill.add(ln)
    for i, ln in enumerate(lines, 1):
        out.append("" if i in kill else ln)
    no_comments = []
    for ln in out:
        # crude but adequate: drop anything after a # that is not inside quotes
        idx = ln.find("#")
        if idx >= 0:
            before = ln[:idx]
            if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                ln = before
        no_comments.append(ln)
    return "\n".join(no_comments)


def main() -> int:
    findings = []
    for py in sorted(MM.glob("*.py")):
        src = py.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # module-level assignments
        assigned = {}
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and NAME_RE.match(t.id):
                    assigned[t.id] = t.lineno
        if not assigned:
            continue
        # [T66] 精确统计：用 AST 数 **Load 上下文** 的 Name。
        # 第一版我"删掉注释与字符串所在整行"再正则计数 ——
        # 但这会把 **f-string 里的名字**（`f"{ROOT}/x"`）连所在行一起删掉，
        # 于是 `ROOT`/`INFO_FILE` 这类**只出现在 f-string 里**的常量被误报。
        # （实测第一版报 52 个 ASSIGN-ONLY，其中多数是这种假阳性。）
        loads = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loads.add(node.id)
            elif isinstance(node, ast.Attribute):          # mod.CONST
                pass
        # 注释/字符串里的"提及"次数（用于区分 ASSIGN-ONLY 与 UNUSED）
        raw = src
        for name, ln in assigned.items():
            if name in loads:
                continue
            pat = re.compile(r"\b" + re.escape(name) + r"\b")
            mentions = len(pat.findall(raw)) - 1      # minus its own assignment
            findings.append((str(py.name), name, ln, 0, mentions))

    print("=" * 96)
    print("「声明了但从未被读取」的模块级常量（本会话最大缺陷的同类）")
    print("=" * 96)
    defects = [f for f in findings if f[4] > 0]
    dead = [f for f in findings if f[4] == 0]
    print(f"  总计 {len(findings)} 个")
    print(f"    · ASSIGN-ONLY（有注释提及、零代码读取）: **{len(defects)}**  <- 高危")
    print(f"    · UNUSED（完全没人提）                : {len(dead)}")
    print()
    if defects:
        print("  ── ASSIGN-ONLY（正是 `_EXIT_TAKER_AFTER_SEC` 那一类）──")
        print(f"  {'file':<24}{'name':<44}{'line':>6}{'注释提及':>9}")
        for f, n, ln, r, m in sorted(defects, key=lambda x: -x[4]):
            print(f"  {f:<24}{n[:43]:<44}{ln:>6}{m:>9}")
    print()
    if dead:
        print("  ── UNUSED（列出前 15 个）──")
        print(f"  {'file':<24}{'name':<44}{'line':>6}")
        for f, n, ln, r, m in sorted(dead)[:15]:
            print(f"  {f:<24}{n[:43]:<44}{ln:>6}")
    print()
    print("  读法：ASSIGN-ONLY 说明**有人以为它在工作**（写了注释解释它），")
    print("        但代码里没有任何一处用它 ⇒ 正是 T17 踩的那个坑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
