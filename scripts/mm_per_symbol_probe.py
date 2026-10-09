import json, sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

d = json.load(open(r"D:\001Alpha\Hyper-Alpha-Arena\logs\mm_sweep_aligned_incumbent.json", encoding="utf-8"))
rows = d["rows"]
print("combos:", len(rows), " delays:", d["delays_ms"], " span_h:", d.get("span_h"))
for r in rows:
    print("diff:", r["diff"])
    for e in r["per"]:
        print(f"  {e['delay_ms']/1000:.1f}s fills={e['fills']} net_usd={e['net_usd']} "
              f"net_bp={e['net_bp']} maker_bp={e.get('maker_bp')} "
              f"flatten_bp={e.get('flatten_bp')} flattens={e.get('flattens')} "
              f"max_dd_pct={e.get('max_dd_pct')} side={e.get('side_counts')}")

# 看回放报告 per_symbol —— 需要重跑一次拿 per_symbol？sweep JSON 没有 per_symbol。
# 直接补跑一次回放并打印 per_symbol。
from backend.services import lane_registry as reg
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams
from backend.services.market_maker.portfolio_replay import _load_all, replay_portfolio
from datetime import datetime, timezone
import numpy as np

lane = reg.get_lane("mm_asterdex") or {}
meta = dict(lane.get("meta") or {})
symbols = list(meta.get("symbols") or [])
venue = str(meta.get("venue") or "asterdex")
cur = dict(meta.get("params") or {})
qp = QuoteParams(**{k: v for k, v in cur.items() if k in QuoteParams.__dataclass_fields__})
lim = LaneRiskLimits(**{k: v for k, v in cur.items() if k in LaneRiskLimits.__dataclass_fields__})
vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})

since_ms = int(datetime.fromisoformat("2026-09-15T08:00:00+08:00").timestamp() * 1000)
data = _load_all(symbols, venue)
sub = {}
for s in symbols:
    dd = data[s]
    a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
    sub[s] = {k: dd[k][a2:] for k in ("ots", "bb", "ba")}
    for k in ("tts", "lo", "hi", "sv", "bv", "tmk"):
        sub[s][k] = dd[k][int(np.searchsorted(dd["tts"], since_ms, "left")):]
seed = {}
for s in symbols:
    dd = data[s]
    a2 = int(np.searchsorted(dd["ots"], since_ms, "left"))
    lo_i = max(0, a2 - 240)
    seed[s] = [x for x in (float((dd["bb"][k] + dd["ba"][k]) / 2.0)
               for k in range(lo_i, a2)) if x > 0]

r = replay_portfolio(symbols, venue=venue, equity=300.0, params=qp, limits=lim,
                     fill_notional=300.0, data=sub, vol_baseline=(vb or None),
                     enforce_lane_limits=True, tick_delay_ms=25100.0,
                     fill_notional_ratio=0.1, mid_hist_seed=(seed or None))
print("\n== per_symbol（08:00→now, 25.1s 口径, 在位配置）==")
for s, v in sorted(r["per_symbol"].items()):
    print(f"  {s:<5} fills={v['fills']:>4}  net_usd={v['net_usd']:>+8.3f}  net_bp={v['net_bp']:>+8.3f}")
print("  flattens:", r.get("flattens"), " flatten_net_bp:", r.get("flatten_net_bp"),
      " maker_net_bp:", r.get("maker_net_bp"))
print("  skip_counts:", dict(sorted((r.get("skip_counts") or {}).items(), key=lambda kv: -kv[1])[:10]))

# [F234] 分币 × 分腿型（被动腿 vs 平仓腿）分解：决定"砍币/留币"不能只看总数 ✗
from collections import defaultdict
mk, fl = defaultdict(lambda: [0.0, 0.0]), defaultdict(lambda: [0.0, 0.0])
for x in r.get("fills_log") or []:
    tgt = fl if x.get("flatten") else mk
    tgt[x["symbol"]][0] += float(x.get("net_usd") or 0.0)
    tgt[x["symbol"]][1] += float(x.get("notional") or 0.0)
print("\n== 分币 × 分腿型（被动/平仓，bp=net_usd/notional×1e4）==")
print(f"{'币':<5}{'被动net$':>10}{'被动bp':>9}{'被动名义$':>11}{'平仓net$':>10}{'平仓bp':>9}{'平仓次数':>8}")
fl_n = defaultdict(int)
for x in r.get("fills_log") or []:
    if x.get("flatten"):
        fl_n[x["symbol"]] += 1
for s in sorted(set(list(mk) + list(fl))):
    m_usd, m_ntl = mk[s]
    f_usd, f_ntl = fl[s]
    print(f"{s:<5}{m_usd:>+10.3f}{(m_usd/m_ntl*1e4 if m_ntl else 0):>+9.2f}{m_ntl:>11.0f}"
          f"{f_usd:>+10.3f}{(f_usd/f_ntl*1e4 if f_ntl else 0):>+9.2f}{fl_n[s]:>8}")
