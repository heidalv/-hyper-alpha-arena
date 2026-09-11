# -*- coding: utf-8 -*-
"""learned 多头门线上行为探针（只读）：对主流币打印 daily regime + learned 判定。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from backend.services.full_auto.midlong_circuit_gate import (  # noqa: E402
    _daily_regime, _long_mode, _long_learned_ok,
)

print("MIDLONG_LONG_MODE =", _long_mode())
for sym in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "UNI", "ADA", "AVAX", "LINK"):
    reg = _daily_regime(sym)
    ok, why = _long_learned_ok(sym, reg) if reg in ("up", "chop") else (False, "regime_skip")
    print(f"  {sym:<6} regime={reg:<6} learned={ok}  {why}")
