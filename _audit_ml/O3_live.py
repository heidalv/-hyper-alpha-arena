import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.full_auto.midlong_circuit_gate import _short_mode, _daily_regime, check_midlong_entry
print("short mode:", _short_mode())
print(f"\n{'币':<8}{'日线regime':<12}{'做多':<8}{'做空':<8}")
for sym in ("BTC","ETH","SOL","XRP","UNI","DOGE","ADA","LINK","AVAX","TON"):
    reg = _daily_regime(sym) or "N/A"
    ok_l, r_l = check_midlong_entry(14, sym, side="buy", tier="mid")
    ok_s, r_s = check_midlong_entry(14, sym, side="sell", tier="mid")
    print(f"{sym:<8}{reg:<12}{'放行' if ok_l else '拦截':<8}{'放行' if ok_s else '拦截':<8}")
