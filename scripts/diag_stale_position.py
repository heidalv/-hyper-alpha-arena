# -*- coding: utf-8 -*-
"""查 BNB 超期持仓：运行时状态（qty/opened_ts）与硬上限条件是否满足。只读。"""
import sys
import json
import time
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
cur.execute("""
    SELECT symbol, state_json, updated_ts FROM lane_runtime_state
    WHERE lane_id='mm_asterdex' ORDER BY symbol
""")
now = time.time()
print(f"{'币':<6}{'qty':>13}{'opened_ts':>14}{'年龄s':>9}{'硬上限该触发?':>14}  updated")
for sym, sj, upd in cur.fetchall():
    s = sj if isinstance(sj, dict) else json.loads(sj or "{}")
    qty = float(s.get("qty") or 0.0)
    ots = float(s.get("opened_ts") or 0.0)
    age = (now - ots) if ots > 0 else -1
    if abs(qty) < 1e-12:
        flag = "空仓"
    elif ots <= 0:
        flag = "⚠ opened_ts=0"
    elif age > 300:
        flag = "⚠ 超限未平"
    else:
        flag = "正常"
    print(f"{sym:<6}{qty:>13.6f}{ots:>14.1f}{age:>9.0f}{flag:>14}  {upd:%H:%M:%S}")

st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8"))
lim = j.get("limits") or {}
print("\n相关开关：timeout_exit_maker_only =", lim.get("timeout_exit_maker_only"),
      "| timeout_hard_taker_sec =", lim.get("timeout_hard_taker_sec"),
      "| max_one_side_seconds =", lim.get("max_one_side_seconds"),
      "| min_hold_seconds =", lim.get("min_hold_seconds"),
      "| reduce_quote_disabled =", lim.get("reduce_quote_disabled"))
print("skip_counts 相关:", {k: v for k, v in (j.get("skip_counts") or {}).items()
                           if "timeout" in k or "exposure" in k})
