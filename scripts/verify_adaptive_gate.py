# -*- coding: utf-8 -*-
"""自适应闸生效验证：逐币读 lane_runtime_state.state_json 的 ar300_hist 统计。只读。"""
import sys
import json
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
print(f"{'币':<6}{'ar300_hist':>11}{'p35门槛':>10}{'当前|r300|':>11}{'会拦?':>7}  updated")
for sym, sj, upd in cur.fetchall():
    st = sj if isinstance(sj, dict) else json.loads(sj or "{}")
    h = st.get("ar300_hist") or []
    mh = st.get("mid_hist") or []
    cur_ar = 0.0
    if len(mh) >= 21 and mh[-21] > 0:
        cur_ar = abs((mh[-1] - mh[-21]) / mh[-21] * 1e4)
    thr = 0.0
    if len(h) >= 60:
        srt = sorted(h)
        thr = srt[min(len(srt) - 1, int(len(srt) * 0.35))]
    blocked = "拦截" if (thr > 0 and cur_ar < thr) else "放行"
    print(f"{sym:<6}{len(h):>11}{thr:>10.2f}{cur_ar:>11.2f}{blocked:>7}  {upd:%H:%M:%S}")
