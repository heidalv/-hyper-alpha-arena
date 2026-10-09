import json, time, urllib.request

BASE = "http://127.0.0.1:8000"


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=120) as r:
        return json.load(r)


def snap(tag):
    rs = get("/api/trading/risk/summary")
    sh = get("/api/trading/lanes/mm_asterdex/shadow")
    pos = [s for s, v in (sh.get("states") or {}).items() if abs(float(v.get("qty") or 0)) > 0]
    det = {s: (round(float(sh["states"][s]["qty"]), 4),
               round(abs(float(sh["states"][s]["qty"])) * float(sh["states"][s]["avg_px"] or 0), 2))
           for s in pos}
    print(f"[{time.strftime('%H:%M:%S')}] {tag}")
    print(f"    gross=${rs.get('gross_exposure_usd')} net=${rs.get('net_exposure_usd')}"
          f" ({rs.get('net_exposure_pct')}% of equity) unrealized=${rs.get('unrealized_usd')}")
    print(f"    持仓 qty/名义: {det}")
    print(f"    ticks={sh.get('ticks')} fills={sh.get('fills')}"
          f" skip={json.dumps(sh.get('skip_counts'), default=str)}")
    print(f"    side={json.dumps(sh.get('side_counts'), default=str)}"
          f" equity={round(float(sh.get('equity') or 0), 2)}")


for i in range(5):
    snap(f"#{i}")
    if i < 4:
        time.sleep(45)
