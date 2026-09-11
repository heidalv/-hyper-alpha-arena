import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.full_auto.midlong_location_gate import location_gate_check, _range_from_klines, _RANGE_CACHE
print("BTC range:", _range_from_klines("BTC"))
ms = {"BTC": {"price": 78900.0}}
print("near-high buy:", location_gate_check("BTC", "buy", tier="mid", regime="ranging", market_summary=ms))
ms2 = {"BTC": {"price": 77700.0}}
print("near-low buy:", location_gate_check("BTC", "buy", tier="mid", regime="ranging", market_summary=ms2))
ms3 = {"BTC": {}}
print("no price (kline fallback):", location_gate_check("BTC", "buy", tier="mid", regime="ranging", market_summary=ms3))
