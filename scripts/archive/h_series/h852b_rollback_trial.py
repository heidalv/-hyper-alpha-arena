# -*- coding: utf-8 -*-
"""[h852b] 回滚止损试跑(避免与"挂单止损阶梯"这个更大的干预混淆),只留单变量。"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from backend.services.market_maker.flow_rules import (  # noqa: E402
    load_learn_params, save_learn_params,
)

old = load_learn_params(ROOT)
new = dict(old)
new["disaster_stop_cap_bp"] = 40.0        # 回滚到原值
save_learn_params(ROOT, new)
print(f"stop_cap: {old.get('disaster_stop_cap_bp')} → "
      f"{load_learn_params(ROOT).get('disaster_stop_cap_bp')}(已回滚)")

pend_path = ROOT / "logs" / "self_tuner_pending.json"
try:
    pend = json.loads(pend_path.read_text(encoding="utf-8"))
except Exception:
    pend = []
kept = []
for e in pend:
    if str(e.get("param")) == "disaster_stop_cap_bp" \
            and float(e.get("new") or 0) == 60.0:
        e["superseded"] = True
        e["superseded_at"] = time.time()
        e["supersede_reason"] = ("h852 挂单止损阶梯(maker_risk 层 + 硬吃单 300s→1800s)"
                                 "是更大的干预;两个变量同时动会让裁定无法归因 ⇒ 回滚止损试跑")
        kept.append(e)
    else:
        kept.append(e)
pend_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"pending 共 {len(kept)} 条,其中已标记 superseded "
      f"{sum(1 for e in kept if e.get('superseded'))} 条")
