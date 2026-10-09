# -*- coding: utf-8 -*-
"""[F237] 连续观测：新配置上线后的实盘行为 vs 模型（F196 纪律：先核对行为一致，再谈收益）。
每 60s 采样一次 /shadow + /risk/summary + /account/unified，采样 N 轮。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
GAP = int(sys.argv[2]) if len(sys.argv) > 2 else 60


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=60) as r:
        return json.load(r)


for i in range(N):
    t0 = time.time()
    try:
        sh = get("/api/trading/lanes/mm_asterdex/shadow")
        ua = get("/api/trading/account/unified")
        lanes = get("/api/trading/lanes")
        h = next((x.get("health") for x in lanes["items"] if x["lane_id"] == "mm_asterdex"), None)
        qty = {k: round(v.get("qty"), 6) for k, v in (sh.get("states") or {}).items()
               if abs(v.get("qty") or 0) > 1e-9}
        print(f"[{time.strftime('%H:%M:%S')}] ticks={sh.get('ticks'):>4} fills={sh.get('fills'):>3}"
              f" σ_all={sh.get('avg_sigma_all')} w={json.dumps(sh.get('avg_width_bp'), default=str)}"
              f" side={json.dumps(sh.get('side_counts'), default=str)}"
              f" skip={json.dumps(dict(sorted((sh.get('skip_counts') or {}).items(), key=lambda kv: -kv[1])[:5]), default=str)}"
              f" qty={json.dumps(qty, default=str)}"
              f" eq={ua['account']['equity']} realized={ua['account']['realized_pnl']}"
              f" health={json.dumps(h, ensure_ascii=False)[:90]}")
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ERR {e}")
    time.sleep(max(0, GAP - (time.time() - t0)))
