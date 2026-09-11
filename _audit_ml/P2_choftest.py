import os, sys, importlib
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
os.environ["MIDLONG_SHORT_MODE"]="regime_gated"
from backend.services.full_auto import midlong_circuit_gate as g
importlib.reload(g)
g._state={}; g._loaded=True
print("chop_flat enabled:", g._chop_flat_enabled())
for reg in ("up","chop","down"):
    g._daily_regime = lambda sym, _r=reg: _r
    bl, rl = g.check_midlong_entry(14,"BTC",side="buy",tier="mid")
    bs, rs = g.check_midlong_entry(14,"BTC",side="sell",tier="mid")
    print(f"  regime={reg:<5} 做多={'放行' if bl else '拦截':<5} 做空={'放行' if bs else '拦截':<5}")
print("\n--- 开启 chop flat ---")
os.environ["MIDLONG_CHOP_MODE"]="flat"
for reg in ("up","chop","down"):
    g._daily_regime = lambda sym, _r=reg: _r
    bl, rl = g.check_midlong_entry(14,"BTC",side="buy",tier="mid")
    bs, rs = g.check_midlong_entry(14,"BTC",side="sell",tier="mid")
    print(f"  regime={reg:<5} 做多={'放行' if bl else '拦截':<5} 做空={'放行' if bs else '拦截':<5}")
