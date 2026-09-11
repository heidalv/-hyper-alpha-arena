# -*- coding: utf-8 -*-
"""Z190（P9 之二）：确认损坏形态（`?` 丢失）与是否存在可恢复的备份。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
ENV = ROOT / ".env"
lines = ENV.read_text(encoding="utf-8-sig", errors="replace").split("\n")

q_lines = [i for i, ln in enumerate(lines, 1) if re.search(r"\?{1,}", ln)]
q_comment = [i for i in q_lines if lines[i - 1].strip().startswith("#")]
print(f".env 共 {len(lines)} 行；含 '?' 的行 = {len(q_lines)}（其中注释行 {len(q_comment)}）")
print("样例（前 6 行）:")
for i in q_lines[:6]:
    print(f"  [{i}] {lines[i-1][:100]}")

print("\n=== 16 个'可疑乱码'行原样 ===")
suspect = [937, 965, 977, 989, 1029]
for i in suspect:
    if i <= len(lines):
        print(f"  [{i}] {lines[i-1][:110]}")

print("\n=== 寻找 .env 备份 / 历史副本 ===")
cands = []
for pat in ("**/.env*", "**/*env*.bak", "**/*.env.*", "**/.env_*", "**/env.backup*"):
    for p in ROOT.glob(pat):
        s = str(p)
        if any(x in s for x in (".venv", "node_modules", "__pycache__", "site-packages")):
            continue
        if p.is_file():
            cands.append(p)
seen = set()
for p in cands:
    if p in seen:
        continue
    seen.add(p)
    try:
        st = p.stat()
        first = p.read_text(encoding="utf-8-sig", errors="replace").split("\n")[:1]
        print(f"  {p.relative_to(ROOT)}  {st.st_size/1024:.1f}KB  mtime={st.st_mtime}")
        print(f"      首行: {first[0][:80] if first else ''}")
    except Exception as e:  # noqa: BLE001
        print(f"  {p} 读取失败 {e}")

print("\n=== git 历史里是否有 .env ===")
import subprocess

try:
    out = subprocess.run(["git", "log", "--oneline", "--all", "--", ".env"],
                         cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    print("  git log .env ->", (out.stdout or "(空)").strip()[:300] or "(空)")
    out2 = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"],
                          cwd=str(ROOT), capture_output=True, text=True, timeout=30)
    print("  .env 是否被 git 跟踪:", out2.returncode == 0)
except Exception as e:  # noqa: BLE001
    print("  git 查询失败:", e)
