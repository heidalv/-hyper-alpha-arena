import sys, json
sys.path.insert(0, ".")
from backend.services.scalp_meta_trainer import _load_settled_rows, _build_regime_frame, _dedup_rows

rows = _load_settled_rows()
print("loaded", len(rows))
full_frame = _build_regime_frame(rows)
print("frame entries:", len(full_frame) if full_frame else 0)
for i in range(len(rows)):
    rows[i]["_orig_idx"] = i
ded = _dedup_rows(rows)
print("deduped:", len(ded))
# 覆盖率
mapped = {}
for new_i, r in enumerate(ded):
    oi = r.get("_orig_idx")
    if oi is not None and full_frame and oi in full_frame:
        mapped[new_i] = full_frame[oi]
print("dedup rows with regime features:", len(mapped), f"({100.0*len(mapped)/max(1,len(ded)):.1f}%)")
# 规则检查: 成熟期滚动特征 vs 胜负
pos = [mapped[k]["roll_fwd20"] for k in mapped if mapped[k].get("roll_fwd20") is not None]
print("roll_fwd20 count:", len(pos))
import collections
buck = collections.defaultdict(lambda: [0, 0, 0.0])
for new_i, r in enumerate(ded):
    f = mapped.get(new_i) or {}
    rf = f.get("roll_fwd20")
    if rf is None: continue
    k = "pos" if rf > 0 else "neg"
    buck[k][0] += 1
    buck[k][1] += r["win"]
    buck[k][2] += r["net_ret"]
for k, (n, w, net) in buck.items():
    print(f"roll_fwd20 {k}: n={n} wr={100.0*w/n:.1f}% net={net/n*100:.4f}%")
# 看最近一条 UNI 的帧
last_uni = [ (new_i, ded[new_i], mapped.get(new_i)) for new_i in range(len(ded)) if ded[new_i]["symbol"]=="UNI" ][-5:]
for new_i, r, f in last_uni:
    print("UNI", r["created_at"], "win", r["win"], "frame", f)
