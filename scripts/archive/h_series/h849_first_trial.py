# -*- coding: utf-8 -*-
"""[h849] 第一次真正的参数试跑(单变量)+ pending 登记。

依据(桥 23:58):
  · 49 条往返平均 +13.11bp,强平 0;近 1h 43 条 ≥ 30 门槛;
  · 唯一失血口 = 灾难止损(taker_stop −21~−57bp;吃单费占比 85%),
    而 0 费挂单往返 +2~+106bp;
  · 经验层三档全转正(+6.7/+14.9/+50.7bp)⇒ 打到的挂单成交不毒。
⇒ 单变量:`disaster_stop_cap_bp` 40 → 60(放宽止损,让更多仓走 0 费挂单离场;
  floor 保持 15 不动 —— 严格单变量)。
  rollback = 40。裁定:30 分钟后用 should_rollback_flow 三关。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.services.market_maker.flow_rules import (  # noqa: E402
    load_learn_params, save_learn_params, window_stats,
)

rows = []
p = ROOT / "data" / "flow_roundtrip_log.jsonl"
if p.exists():
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
now = time.time()
before = window_stats(rows, now - 3600, now)
print(f"试跑前(近 1h):n={before['n']} 平均 y={before['mean_y']:+.2f}bp "
      f"吃单费占比={before['taker_fee_share']*100:.0f}% "
      f"挂单成交率={before['fill_rate']*100:.0f}% 强平={before['liquidations']}")

old = load_learn_params(ROOT)
print(f"改前 stop_cap={old.get('disaster_stop_cap_bp')} floor={old.get('disaster_stop_floor_bp')}")
new = dict(old)
new["disaster_stop_cap_bp"] = 60.0
save_learn_params(ROOT, new)
chk = load_learn_params(ROOT)
print(f"改后 stop_cap={chk.get('disaster_stop_cap_bp')} floor={chk.get('disaster_stop_floor_bp')}"
      f" ⇒ {'生效 ✓' if chk.get('disaster_stop_cap_bp') == 60.0 else '未生效 ✗'}")

pend_path = ROOT / "logs" / "self_tuner_pending.json"
try:
    pend = json.loads(pend_path.read_text(encoding="utf-8"))
    if not isinstance(pend, list):
        pend = []
except Exception:
    pend = []
entry = {
    "param": "disaster_stop_cap_bp", "old": 40.0, "new": 60.0, "rollback": 40.0,
    "applied_at": now, "verdict_at": now + 1800,
    "verdict_metric": "roundtrip_y", "era": "flow",
    "verdict_rule": "should_rollback_flow:平均 y ≥ 改前 80% 且吃单费占比不升 "
                    "且挂单成交率不塌且强平=0",
    "before": {"n": before["n"], "mean_y": round(before["mean_y"], 3),
               "taker_fee_share": round(before["taker_fee_share"], 4),
               "fill_rate": round(before["fill_rate"], 4)},
    "reason": "h849 第一次单变量试跑:止损上限放宽,减少被噪音吃单打掉的仓",
}
if not any(e.get("param") == "disaster_stop_cap_bp" and
           float(e.get("new") or 0) == 60.0 for e in pend):
    pend.append(entry)
    pend_path.write_text(json.dumps(pend, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ pending 已登记(共 {len(pend)} 条,era=flow "
          f"{sum(1 for e in pend if str(e.get('era')) == 'flow')} 条)")
else:
    print("pending 里已有同款条目,跳过")
print(f"  裁定时间:{time.strftime('%H:%M:%S', time.localtime(now + 1800))}")
