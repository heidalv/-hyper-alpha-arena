# -*- coding: utf-8 -*-
"""[§84 执行 2026-09-11 / 决策 P27-A] 把 BREAKER_EVIDENCE_STALE_DAYS=7 写入 .env（BOM/CRLF 保留 + 备份）。"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
env = Path(r"D:\001Alpha\Hyper-Alpha-Arena\.env")
text = env.read_bytes().decode("utf-8-sig")
if "BREAKER_EVIDENCE_STALE_DAYS" in text:
    print("已存在，跳过")
    raise SystemExit(0)
bak = env.with_name(env.name + f".bak_p27a_{time.strftime('%Y%m%d_%H%M%S')}")
shutil.copy2(env, bak)
print("备份:", bak.name)
line = ("\r\n# [§84 执行 2026-09-11 / 决策 P27-A] 熔断证据新鲜度（天）：过期证据只记录不抑制；0=关闭\r\n"
        "BREAKER_EVIDENCE_STALE_DAYS=7\r\n")
env.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8") + line.encode("utf-8"))
new = env.read_bytes().decode("utf-8-sig")
print("键数:", len([l for l in new.splitlines() if l.strip() and not l.strip().startswith("#") and "=" in l]))
print("BOM:", env.read_bytes()[:3] == b"\xef\xbb\xbf",
      "| 行:", [l for l in new.splitlines() if l.startswith("BREAKER_EVIDENCE_STALE_DAYS")])
