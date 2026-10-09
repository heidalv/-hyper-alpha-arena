"""h603 — 给 h5xx/h6xx 只读分析脚本补上 stdout UTF-8 守卫（R187）。

背景（R187 实测）：在 pwsh 里用管道读取脚本输出时，Python 的 stdout 编码退化成
GBK，脚本打印 ⇒ / 中文时抛 UnicodeEncodeError 直接崩，输出只剩前半段 —— 看起来
像「脚本没问题」，实际是**读数被截断**。h582 本轮就是这样挂的。

本脚本只对**缺失守卫**的脚本插入一个幂等兼容块（自带 import sys as _sys，不与文件
里已有的 import sys 冲突）：

    import sys as _sys

    try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

用法：
  python scripts/h603_fix_stdout_guard.py            # 预演（不改文件）
  python scripts/h603_fix_stdout_guard.py --apply    # 真改
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

BLOCK = (
    "import sys as _sys\n"
    "\n"
    "try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃\n"
    "    _sys.stdout.reconfigure(encoding=\"utf-8\", errors=\"replace\")\n"
    "except Exception:\n"
    "    pass\n"
)

NAME_RE = re.compile(r"^h(\d{3})[a-z0-9]*_.*\.py$")
MIN_NUM = 550


def _insert_pos(text: str) -> int:
    """返回插入点（字符偏移）：__future__ 导入之后，否则模块 docstring 之后。"""
    lines = text.splitlines(keepends=True)
    off = 0
    fut = None
    for i, ln in enumerate(lines):
        if ln.startswith("from __future__ import"):
            fut = off + len(ln)
        off += len(ln)
    if fut is not None:
        return fut
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        end = tree.body[0].end_lineno or 1
        return sum(len(x) for x in lines[:end])
    return 0


def _has_guard(text: str) -> bool:
    return "reconfigure(encoding" in text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写文件")
    args = ap.parse_args()

    print("=" * 84)
    print("h603 — 只读脚本 stdout UTF-8 守卫补齐（幂等）")
    print("=" * 84)

    patched, already, skipped = [], [], []
    for p in sorted(SCRIPTS.glob("h*.py")):
        m = NAME_RE.match(p.name)
        if not m or int(m.group(1)) < MIN_NUM:
            continue
        raw = p.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        if p.name == Path(__file__).name:
            skipped.append((p.name, "自身"))
            continue
        if _has_guard(text):
            already.append(p.name)
            continue
        if all(ord(ch) < 128 for ch in text):
            skipped.append((p.name, "纯 ASCII（无需守卫）"))
            continue
        pos = _insert_pos(text)
        new = text[:pos]
        if not new.endswith("\n"):
            new += "\n"
        if not new.endswith("\n\n"):
            new += "\n"
        new += BLOCK + text[pos:]
        try:
            ast.parse(new)
        except SyntaxError as e:
            skipped.append((p.name, f"补丁后语法错，跳过：{e}"))
            continue
        if args.apply:
            p.write_text(new, encoding="utf-8", newline="\n")
            back = p.read_text(encoding="utf-8")
            assert "reconfigure(encoding" in back, p.name
            assert not p.read_bytes().startswith(b"\xef\xbb\xbf"), f"BOM! {p.name}"
        patched.append(p.name + (" (BOM已去)" if bom else ""))

    print(f"  待补 / 已补：{len(patched)} 个")
    for n in patched:
        print(f"    + {n}")
    print(f"  已有守卫：{len(already)} 个（跳过）")
    for n, why in skipped:
        print(f"  - 跳过 {n}：{why}")
    print("-" * 84)
    print(f"  模式：{'已写入' if args.apply else '预演（未改文件）'}")
    if not args.apply and patched:
        print("  下一步：加 --apply 落地")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
