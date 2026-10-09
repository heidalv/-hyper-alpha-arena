# -*- coding: utf-8 -*-
"""校验 A/C 两处 .env 改动：值是否正确、有没有破坏编码基线（含 '?' 的行数）。只读。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / ".env"
raw = p.read_bytes()
txt = raw.decode("utf-8", errors="replace")
lines = txt.splitlines()

print(f"字节数 = {len(raw):,}   行数 = {len(lines):,}")
print(f"U+FEFF 个数 = {raw.count(b'\xef\xbb\xbf')}")

print("\n[1] A/C 两个键的当前值")
for key in ("MIDLONG_LOCATION_PAPER_SHRINK_CEILING", "REENTRY_SL_COOLDOWN_SEC_MID",
            "MIDLONG_LOCATION_PAPER_SHRINK_ENABLED", "MIDLONG_LOCATION_PAPER_SHRINK_MULT",
            "REENTRY_LOSS_COOLDOWN_SEC_MID"):
    hits = [ln.strip() for ln in lines if ln.strip().startswith(key + "=")]
    print(f"    {key:42s} {hits}")

print("\n[2] 编码基线（治理项：含 ≥5 个 '?' 的行数）")
bad = [i + 1 for i, ln in enumerate(lines) if ln.count("?") >= 5]
print(f"    含 ≥5 个 '?' 的行数 = {len(bad)}（历史基线 ≤352）")
new_bad = [i for i in bad if i >= 1900 or 1140 <= i <= 1175]
print(f"    我改动区域附近（1140-1175 / 1900+）的此类行 = {new_bad}")

print("\n[3] 我新增的注释行是否可读（抽查）")
for i, ln in enumerate(lines, 1):
    if "解冻·选项" in ln:
        print(f"    {i}: {ln}")

print("\n[4] 生效值是否含 '?'（治理要求：不得含）")
val_bad = [(i + 1, ln.strip()) for i, ln in enumerate(lines)
           if "=" in ln and not ln.strip().startswith("#")
           and "?" in ln.split("=", 1)[1]]
print(f"    生效值含 '?' 的键 = {len(val_bad)}")
for i, ln in val_bad[:5]:
    print(f"      {i}: {ln[:90]}")
