import os, sys, importlib
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
os.environ["MIDLONG_SHORT_MODE"]="regime_gated"
from backend.services.full_auto import midlong_circuit_gate as g
importlib.reload(g)
g._state={}; g._loaded=True
def probe(tag):
    print(f"--- {tag} (chop_flat={g._chop_flat_enabled()}) ---")
    for reg in ("up","chop","down"):
        g._daily_regime = lambda sym, _r=reg: _r
        bl, rl = g.check_midlong_entry(14,"BTC",side="buy",tier="mid")
        bs, rs = g.check_midlong_entry(14,"BTC",side="sell",tier="mid")
        print(f"  regime={reg:<5} long={'ALLOW' if bl else 'BLOCK'} short={'ALLOW' if bs else 'BLOCK'}"
              f"  long_reason={rl[:40]} short_reason={rs[:40]}")
probe("default (long_only)")
os.environ["MIDLONG_CHOP_MODE"]="flat"
probe("chop flat")
