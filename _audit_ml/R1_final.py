import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.full_auto.midlong_circuit_gate import _short_mode, _chop_flat_enabled, _daily_regime
print("=== 重启后配置生效 ===")
print("  MIDLONG_SHORT_MODE =", _short_mode())
print("  chop_flat_enabled  =", _chop_flat_enabled(), "(默认 long_only)")
print("  MIDLONG_CHOP_MODE  =", os.getenv("MIDLONG_CHOP_MODE"))
print("\n=== 当前各币日线 regime ===")
for sym in ("BTC","ETH","SOL","XRP","UNI","DOGE","ADA","AVAX","LINK","TON"):
    print(f"  {sym:<6} {_daily_regime(sym) or 'N/A'}")
