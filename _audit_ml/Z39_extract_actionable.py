# -*- coding: utf-8 -*-
"""Z39：从 config_effective_audit.json 提取**真正可行动**的条目。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
d = json.load(open(ROOT / "data" / "config_effective_audit.json", encoding="utf-8"))

hits = [x for x in d["falsy_sites"] if x["severity"] == "HIGH" and x["safety"]]
print(f"=== HIGH + 安全键 站点 = {len(hits)} ===")
seen = set()
for h in hits:
    k = (h["file"], h["snippet"][:70])
    if k in seen:
        continue
    seen.add(k)
    print(f"  {h['file']}")
    print(f"      {h['snippet'][:130]}")

print("\n=== 含 `or default` 的配置助手（左值为已解析数值时才有害）===")
for h in d["cfg_helpers"]:
    if not h["dangerous"]:
        continue
    print(f"  {h['file']:<58}{h['fn']}")
    print(f"      {h['body'][:130]}")

print("\n=== 真·死配置（.env 有、settings 无、全仓无引用）===")
print(f"  n={len(d['findings']['env_truly_dead'])}")
print("  " + ", ".join(d["findings"]["env_truly_dead"][:50]))

print("\n=== 安全类键未被 env_registry 登记 ===")
sf = [k for k in d["findings"]["settings_not_in_registry"]
      if any(t in k for t in ("MIDLONG", "RISK", "TIER", "EXIT", "PAPER", "POSITION", "TP_", "SL_"))]
print(f"  n={len(sf)}: " + ", ".join(sf))
