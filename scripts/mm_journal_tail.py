import io, sys, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services.market_maker.evolution import JOURNAL_PATH, read_journal
rows = read_journal(200)
print(f"journal {JOURNAL_PATH} 共 {len(rows)} 条，末 6 条摘要:")
for r in rows[-6:]:
    e = r.get("event") or r.get("type") or ""
    ts = r.get("ts") or ""
    if e:
        print(f"  {ts}  event={e}  net_bp={r.get('net_bp')}  applied={r.get('applied')}")
        rest = r.get("restored")
        if rest:
            print(f"      restored GRID keys: {json.dumps({k: rest.get(k) for k in ('w_base_bp','k_inv','k_vol','frozen_width_bp','min_width_reduce_bp','ofi_block_threshold','max_one_side_seconds','frozen_max_move_bp','frozen_lookback')}, ensure_ascii=False)}")
    else:
        print(f"  {ts}  round  mode={r.get('mode')}  applied={r.get('applied')}  decision={json.dumps(r.get('decision'), ensure_ascii=False)[:140]}")
import os
print("\nMM_AUTO_EVOLVE =", repr(os.getenv("MM_AUTO_EVOLVE")))
print("MM_EVOLUTION_SINCE =", repr(os.getenv("MM_EVOLUTION_SINCE")))
