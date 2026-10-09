"""h614 — 全脚本语法体检（R201）：一次性找出所有 `ast.parse` 不过的脚本。

为什么需要：本轮我自己在 3 个文件里写错了**嵌套 ASCII 引号**（中文串里再写 `"..."`），
每次都是"跑到才报错"。与其一个个撞，不如一条命令扫全场 ✓。
（`h603` 给 35 个脚本补 UTF-8 守卫时全量 parse 过一次；此后新增的文件没再全量扫。）

用法：python scripts/h614_syntax_sweep.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    bad = []
    n = 0
    for p in sorted((ROOT / "scripts").glob("*.py")):
        if p.name == pathlib.Path(__file__).name:
            continue
        n += 1
        try:
            ast.parse(p.read_text(encoding="utf-8-sig"))
        except SyntaxError as e:
            bad.append((p.name, e.lineno, (e.msg or "")[:60]))
        except Exception as e:  # noqa: BLE001
            bad.append((p.name, 0, f"{type(e).__name__}: {str(e)[:50]}"))
    print("=" * 78)
    print(f"h614 — 全脚本语法体检：检查 {n} 个文件")
    print("=" * 78)
    if not bad:
        print("✓ 全部通过 ✓")
        return 0
    print(f"✗ {len(bad)} 个文件有语法错：")
    for name, ln, msg in bad:
        print(f"    {name}:{ln}  {msg}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
