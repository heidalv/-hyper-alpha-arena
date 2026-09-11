import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
try:
    from backend.services.unified_data_pool import unified_data_pool
    ms = None
    for fn in ('get_market_summary','get_summary','market_summary','build_market_summary','snapshot'):
        f = getattr(unified_data_pool, fn, None)
        if callable(f):
            try:
                ms = f()
                print("via", fn)
                break
            except Exception as e:
                print("try", fn, "err", str(e)[:120])
    if not isinstance(ms, dict):
        print("market summary not obtained; type=", type(ms))
        print([m for m in dir(unified_data_pool) if 'summ' in m.lower() or 'snap' in m.lower()][:20])
    else:
        syms = list(ms.keys())[:3]
        print("symbols sample:", syms)
        for s in syms:
            blk = ms[s]
            if isinstance(blk, dict):
                print(s, "keys:", sorted(blk.keys())[:40])
                ind = blk.get('indicators_1h')
                if isinstance(ind, dict):
                    print("  indicators_1h keys:", sorted(ind.keys()))
except Exception as e:
    import traceback; traceback.print_exc()
