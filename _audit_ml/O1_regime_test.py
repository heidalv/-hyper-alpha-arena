import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
os.environ["MIDLONG_SHORT_MODE"]="regime_gated"
import importlib
from backend.services.full_auto import midlong_circuit_gate as g
importlib.reload(g)
print("short mode:", g._short_mode())
for sym in ("BTC","ETH","SOL","XRP","UNI","XPL","DOGE","ADA","LINK"):
    reg = g._daily_regime(sym)
    print(f"  {sym:<6} 日线 regime = {reg or 'N/A'}")
