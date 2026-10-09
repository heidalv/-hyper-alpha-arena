"""h607 — 探明判定产物的**窗口字段**（为"延长后跨昼夜"的预注册做准备；R195）。

只读。打印 `h463_verdict_dryrun.json` 里与窗口/组成有关的字段，用来决定：
  · 能否从产物本身算出试跑窗的**昼夜构成**（延长到 25h 时 A 基线就不再同钟点 ✗）；
  · 若不能，`h599` 就只能用 `hours` 做近似判据。
"""
from __future__ import annotations

import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "research_l1" / "out" / "h463_verdict_dryrun.json"


def walk(obj, prefix="", depth=0):
    if depth > 3:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                print(f"  {p}: {type(v).__name__}(len={len(v)})")
                walk(v, p, depth + 1)
            else:
                s = str(v)
                print(f"  {p} = {s[:70]}")
    elif isinstance(obj, list) and obj:
        print(f"  {prefix}[0]: {str(obj[0])[:70]}")


def main() -> int:
    print("=" * 84)
    print("h607 — 判定产物的窗口字段探查（只读）")
    print("=" * 84)
    if not SRC.exists():
        print(f"✗ 找不到 {SRC}")
        return 1
    v = json.loads(SRC.read_text(encoding="utf-8"))
    print(f"  文件：{SRC.name}（{SRC.stat().st_size} 字节）")
    walk(v)
    print("\n  判读：若 `trial` 里没有 started_at/until 之类字段 ⇒ "
          "`h599` 只能用 `hours` 近似判断是否已跨越夜间 ✗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
