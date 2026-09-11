import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.full_auto.midlong_circuit_gate import _down_short_enabled, _short_learned_ok, _daily_regime
print("down_short_mode:", _down_short_enabled())
print("\n=== learned 空头特征实测（真实数据）===")
for sym in ("BTC","ETH","SOL","XRP","UNI","DOGE","ADA","AVAX","LINK"):
    reg = _daily_regime(sym)
    ok, why = _short_learned_ok(sym)
    print(f"  {sym:<6} regime={reg:<5} learned={ok} {why}")
