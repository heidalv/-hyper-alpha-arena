# -*- coding: utf-8 -*-
"""Z191（P9 之三）：备份里的注释**能不能还原**（试多种解码链），以及能覆盖多少键。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
ENV = ROOT / ".env"

BACKUPS = [
    ".env.bak_20260821",
    ".env.bak_20260825_pwin",
    ".env.bak_v3_20260903_164735",
    ".env.framework_rollout_backup_20260710",
]


def try_chains(line: str) -> dict[str, str]:
    out = {}
    try:
        out["gbk→utf8"] = line.encode("gbk").decode("utf-8")
    except Exception:
        pass
    try:
        out["gbk→utf8×2"] = line.encode("gbk").decode("utf-8").encode("gbk").decode("utf-8")
    except Exception:
        pass
    try:
        out["latin1→utf8"] = line.encode("latin-1").decode("utf-8")
    except Exception:
        pass
    try:
        out["cp936→utf8"] = line.encode("cp936").decode("utf-8")
    except Exception:
        pass
    return out


for name in BACKUPS:
    p = ROOT / name
    if not p.exists():
        print(f"{name}: 不存在")
        continue
    lines = p.read_text(encoding="utf-8-sig", errors="replace").split("\n")
    head = [ln for ln in lines[:8] if ln.strip()]
    print(f"\n=== {name}（{len(lines)} 行）===")
    for ln in head[:3]:
        print("  原:", ln[:80])
        chains = try_chains(ln)
        for k, v in chains.items():
            # 只有当还原结果"更像人话"（含常见汉字且无乱码高频字）才算成功
            good = all(ch not in v for ch in "锛闂鈺閳鎰") and any("\u4e00" <= c <= "\u9fff" for c in v)
            print(f"    {k:12s} {'✅' if good else '  '} {v[:76]}")

# 键覆盖分析：备份里有多少 key 与当前 .env 同名
cur_keys = {}
for ln in ENV.read_text(encoding="utf-8-sig", errors="replace").split("\n"):
    s = ln.strip()
    if s and not s.startswith("#") and "=" in s:
        cur_keys[s.split("=", 1)[0].strip()] = s.split("=", 1)[1].strip()

print("\n=== 备份键与当前键的交集（决定能否按 key 恢复注释）===")
for name in BACKUPS:
    p = ROOT / name
    if not p.exists():
        continue
    bk = {}
    for ln in p.read_text(encoding="utf-8-sig", errors="replace").split("\n"):
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            bk[s.split("=", 1)[0].strip()] = s.split("=", 1)[1].strip()
    same = [k for k in bk if k in cur_keys and bk[k] == cur_keys[k]]
    diff = [k for k in bk if k in cur_keys and bk[k] != cur_keys[k]]
    only_bk = [k for k in bk if k not in cur_keys]
    print(f"  {name}: 备份键 {len(bk)}；与当前同名 {len([k for k in bk if k in cur_keys])}"
          f"（值相同 {len(same)} / 值不同 {len(diff)}）；备份独有 {len(only_bk)}")
