# -*- coding: utf-8 -*-
"""因子质量与透过率根因分析（只读）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

DATA = Path("D:/001Alpha/Hyper-Alpha-Arena/data")
DATA2 = Path("D:/001Alpha/Hyper-Alpha-Arena/backend/data")


def load(name):
    for base in (DATA, DATA2):
        p = base / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  [load fail {name}: {e}]")
                return None
    return None


print("=== 1) discovered_factors.json 状态分布 ===")
store = load("discovered_factors.json")
if isinstance(store, dict):
    print("  keys:", list(store.keys())[:8])
    # 结构可能是 {key: rec} 或 {"candidates": ...}
    items = None
    for k in ("factors", "records", "items"):
        if isinstance(store.get(k), list):
            items = store[k]
            break
    if items is None and isinstance(store, dict):
        # 尝试 t{tenant}:factor_id 键结构
        items = [v for k, v in store.items() if isinstance(v, dict) and "status" in v]
        if len(items) < 5:
            items = [v for k, v in store.items() if isinstance(v, dict)]
    if items:
        from collections import Counter
        status = Counter(str(i.get("status", "?")) for i in items)
        print("  total:", len(items), "status:", dict(status))
        act = [i for i in items if str(i.get("status")) == "active"]
        cand = [i for i in items if str(i.get("status")) == "candidate"]
        rej = [i for i in items if str(i.get("status")) == "rejected"]
        print(f"  active={len(act)} candidate={len(cand)} rejected={len(rej)}")
        def _ic_stats(lst, label):
            ics = [float(i.get("ic_mean", i.get("ic", i.get("final_ic", 0))) or 0) for i in lst]
            icirs = [float(i.get("icir", i.get("ic_ir", 0)) or 0) for i in lst]
            if not ics:
                print(f"  {label}: (无)")
                return
            pos = sum(1 for x in ics if x > 0)
            print(f"  {label}: n={len(ics)} IC>0={pos} ({pos/max(len(ics),1)*100:.0f}%) "
                  f"IC mean={sum(ics)/len(ics):.4f} max={max(ics):.4f} min={min(ics):.4f} "
                  f"ICIR mean={sum(icirs)/max(len(icirs),1):.3f}")
        _ic_stats(act, "active")
        _ic_stats(cand, "candidate")
        # active 样例
        print("  active 前 8:")
        for i in act[:8]:
            print("   ", str(i.get("factor_id") or i.get("id") or "?")[:40],
                  "ic=", round(float(i.get("ic_mean", i.get("ic", 0)) or 0), 4),
                  "status_since=", str(i.get("promoted_at") or i.get("updated_at") or "")[:16])
    else:
        print("  结构无法解析，样例:", str(store)[:300])
else:
    print("  not dict:", str(store)[:200])

print("\n=== 2) factor_runtime_weights.json ===")
rw = load("factor_runtime_weights.json")
print("  ", str(rw)[:600])

print("\n=== 3) factor_decay_status.json ===")
fd = load("factor_decay_status.json")
print("  ", str(fd)[:600])

print("\n=== 4) gp_mine_progress（5m/4h 挖矿进度）===")
for n in ("gp_mine_progress_5m.json", "gp_mine_progress_4h.json", "gp_mine_progress_15m.json"):
    g = load(n)
    if g:
        print(f"  {n}:", str(g)[:400])

print("\n=== 5) factor_slimming_state.json（瘦身/隔离状态）===")
fs = load("factor_slimming_state.json")
print("  ", str(fs)[:500])

print("\n=== 6) midlong_cold_pool_report.json ===")
mc = load("midlong_cold_pool_report.json")
print("  ", str(mc)[:400])

print("\nDONE")
