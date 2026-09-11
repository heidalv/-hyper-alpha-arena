import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.full_auto.midlong_location_gate import _range_from_klines, _RANGE_CACHE, location_gate_check
print("BTC range:", _range_from_klines("BTC"))
print("ETH range:", _range_from_klines("ETH"))
print("cache:", list(_RANGE_CACHE.keys()))
ms = {"BTC": {"price": 0, "current_price": 0}}
print("gate with empty ms:", location_gate_check("BTC", "buy", tier="mid", regime="ranging", market_summary=ms))
