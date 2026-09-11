import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.full_auto.midlong_location_gate import location_gate_check, _range_from_klines
print("=== 位置闸实测（重启后，真实数据）===")
for sym in ("BTC","ETH","SOL","XRP","UNI"):
    hi, lo, last = _range_from_klines(sym)
    if hi is None:
        print(f"  {sym}: 无数据"); continue
    pos = (last-lo)/(hi-lo)*100
    ok_buy, reason_buy, _ = location_gate_check(sym, "buy", tier="mid", regime="ranging",
                                               market_summary={sym: {"price": last}})
    ok_sell, reason_sell, _ = location_gate_check(sym, "sell", tier="mid", regime="ranging",
                                                  market_summary={sym: {"price": last}})
    print(f"  {sym:<5} 24h区间[{lo:.4g},{hi:.4g}] 现价{last:.4g} 分位{pos:5.1f}% | "
          f"buy={'放行' if ok_buy else '拦截'} sell={'放行' if ok_sell else '拦截'}")
    if not ok_buy: print(f"         {reason_buy[:90]}")
