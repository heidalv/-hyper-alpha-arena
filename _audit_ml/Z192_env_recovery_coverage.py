# -*- coding: utf-8 -*-
"""Z192（P9 之四）：更强的解码链尝试 + 从**完好**来源统计可恢复的注释覆盖。

完好来源：`.env.example`、`backend/.env.example`、`.env.framework_rollout_backup_20260710`。
（`.env.bak_20260821/_20260825_pwin` 的注释是另一种多重编码乱码；本脚本试更多链看能否救回。）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
ENV = ROOT / ".env"
SUSPECT = "锛闂鈺閳鎰娴囬鍨"


def readable(s: str) -> bool:
    if not s or not any("\u4e00" <= c <= "\u9fff" for c in s):
        return False
    return not any(c in SUSPECT for c in s)


def chains(line: str) -> dict[str, str]:
    out = {}
    for enc in ("gbk", "gb18030", "cp936", "latin-1", "cp1252"):
        for times in (1, 2, 3):
            cur = line
            try:
                for _ in range(times):
                    cur = cur.encode(enc, errors="ignore").decode("utf-8", errors="ignore")
                out[f"{enc}×{times}"] = cur
            except Exception:
                pass
    return out


print("=== 多重解码链尝试（对最新 mojibake 备份）===")
bak = ROOT / ".env.bak_20260825_pwin"
if bak.exists():
    line = [ln for ln in bak.read_text(encoding="utf-8-sig", errors="replace").split("\n") if ln.strip()][0]
    print("  原:", line[:70])
    hits = [(k, v) for k, v in chains(line).items() if readable(v)]
    if hits:
        for k, v in hits[:5]:
            print(f"  ✅ {k}: {v[:76]}")
    else:
        print("  ❌ 全部链都不可读（结论：该备份的注释无法自动还原）")

print("\n=== 完好来源可提供的「键 → 注释」覆盖 ===")
sources = [
    (".env.example", "示例"),
    ("backend/.env.example", "示例"),
    (".env.framework_rollout_backup_20260710", "2026-07-10 备份"),
]
key_comment: dict[str, tuple[str, str]] = {}
for name, tag in sources:
    p = ROOT / name
    if not p.exists():
        continue
    lines = p.read_text(encoding="utf-8-sig", errors="replace").split("\n")
    pending: list[str] = []
    got = 0
    for ln in lines:
        s = ln.strip()
        if s.startswith("#"):
            t = s.lstrip("# ").strip()
            if t and not re.fullmatch(r"[-=*#\s]+", t):
                pending.append(t)
            continue
        if s and "=" in s:
            k = s.split("=", 1)[0].strip()
            if pending and k not in key_comment:
                key_comment[k] = (" / ".join(pending[-3:]), tag)
                got += 1
            pending = []
        elif not s:
            pending = []
    print(f"  {name}: 提供 {got} 个键的描述")

# 当前 .env 里被 '?' 摧毁的注释块
lines = ENV.read_text(encoding="utf-8-sig", errors="replace").split("\n")
damaged_blocks: list[tuple[int, str]] = []  # (key_lineno, key)
for i, ln in enumerate(lines):
    s = ln.strip()
    if not s.startswith("#") and "=" in s:
        k = s.split("=", 1)[0].strip()
        # 往上看 3 行，若有 '?' 注释则算受损
        for j in range(max(0, i - 3), i):
            if lines[j].strip().startswith("#") and "?" in lines[j]:
                damaged_blocks.append((i + 1, k))
                break

recoverable = [(ln, k, key_comment[k]) for ln, k in damaged_blocks if k in key_comment]
print(f"\n当前 .env 中注释被 '?' 摧毁的键 = {len(damaged_blocks)}")
print(f"  其中可从**完好来源**恢复说明的 = {len(recoverable)}")
for ln, k, (txt, tag) in recoverable[:8]:
    print(f"   [{ln}] {k}: [{tag}] {txt[:70]}")
