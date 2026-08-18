# -*- coding: utf-8 -*-
"""S1/M2 迁移：重开全部「有公式」的被拒候选（registry 空公式记录不动），
随后由后端 boot 验证任务在新门禁（n_trials 累计 + 中性化）下重跑打分。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_PATH = os.path.join("data", "discovered_factors.json")
with open(_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

reopened = 0
skipped_registry = 0
for rec in data.values():
    if not (rec.get("formula") or "").strip():
        skipped_registry += 1
        continue
    if rec.get("status") != "rejected":
        continue
    rec["status"] = "candidate"
    rec["grade"] = None
    rec["scores"] = {}
    rec.pop("scored_at", None)
    rec["updated_at"] = __import__("time").time()
    reopened += 1

with open(_PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"reopened={reopened} registry_skipped={skipped_registry}")
