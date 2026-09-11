# -*- coding: utf-8 -*-
"""Z72: §52 修复的线上核验 —— 当前配额状态 + 重启后审计行是否带具体码。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from sqlalchemy import create_engine  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.risk import daily_quota  # noqa: E402

print("=== A. 当前账户 14 的每日配额 ===")
print("  caps:", daily_quota.all_caps())
db = SessionLocal()
try:
    for b in ("total", "scalp", "trend", "live"):
        try:
            print(f"  used({b}) =", daily_quota.opens_today(db, 14, b))
        except Exception as e:
            print(f"  used({b}) err:", str(e)[:80])
            db.rollback()
    v = daily_quota.check(db, 14, tier="mid", trade_nature="swing", live=False)
    print("  verdict:", v.to_dict())
finally:
    db.close()

print("\n=== B. 审计文件：新格式行（eval_false:*) 是否出现 ===")
p = ROOT / "data" / "midlong_direction_audit.jsonl"
rows = []
with p.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
gen = [r for r in rows if str(r.get("reason")) == "evaluate_and_execute_returned_false"]
new = [r for r in rows if str(r.get("reason") or "").startswith("eval_false:")]
print(f"  总行 {len(rows)}；旧通用原因 {len(gen)}；新格式 {len(new)}")
for r in new[-5:]:
    print("   ", r.get("ts"), r.get("symbol"), r.get("reason"),
          (r.get("extra") or {}).get("block_layer"), str((r.get("extra") or {}).get("block_detail"))[:60])
if not new:
    print("  （重启后尚无拒仓行——下轮观察期核对；修复代码路径已由契约测试覆盖）")
