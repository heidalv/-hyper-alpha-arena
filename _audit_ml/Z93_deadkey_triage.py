# -*- coding: utf-8 -*-
"""Z93: 死键/近名键的 mid/long 相关性 + 安全类交叉分类（逐键可核验）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))
import audit_config_effective as ace  # noqa: E402

rep = ace.build_report(ROOT)
f = rep["findings"]
SAFETY = ace.SAFETY_HINT
MIDLONG = re.compile(r"(MIDLONG|MID_|_MID|LONG|TREND|THESIS|BRAIN|MLTO|EXIT|_SL|SL_|_TP|TP_|"
                     r"RISK|CAP|GATE|COOLDOWN|REENTRY)", re.I)

dead = f["env_truly_dead"]
print(f"=== 真·死键 {len(dead)} 个（.env 设了、settings 未定义、全仓无该键名）===")
for k in dead:
    tags = []
    if SAFETY.search(k):
        tags.append("SAFETY")
    if MIDLONG.search(k):
        tags.append("MIDLONG?")
    print(f"   {k:<42} {'/'.join(tags) or '-'}")

print(f"\n=== 交集：死键 ∩ 安全类（{'、'.join(k for k in dead if SAFETY.search(k))or '无'}）===")
print(f"=== 交集：死键 ∩ mid/long 相关（{'、'.join(k for k in dead if MIDLONG.search(k)) or '无'}）===")

print(f"\n=== 近名误配 {len(f['near_miss'])} 条 ===")
for nm in f["near_miss"]:
    mark = "★" if (SAFETY.search(nm["env_key"]) or MIDLONG.search(nm["env_key"])) else " "
    print(f"  {mark} {nm['env_key']:<44} → 疑似应为 {nm['candidate']:<40} ({nm['score']})")

print(f"\n=== .env 覆盖了安全类键默认值的 {len(f['safety_env_overrides'])} 条 ===")
for o in f["safety_env_overrides"]:
    print(f"   {o['key']:<40} env={o['env']:<12} 默认={o['code_default']:<12} "
          f"直读={o['direct_read']} 引用={o['uses']}")

print(f"\n=== settings 定义了但从未使用 {len(f['settings_never_used'])} 个 ===")
for k in f["settings_never_used"][:40]:
    mark = "★" if (SAFETY.search(k) or MIDLONG.search(k)) else " "
    print(f"  {mark} {k}")
