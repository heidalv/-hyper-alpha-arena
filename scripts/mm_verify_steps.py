import io, sys, json, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

url = "https://fapi.asterdex.com/fapi/v1/exchangeInfo"
try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    print("exchangeInfo ok, symbols =", len(data.get("symbols") or []))
    want = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"}
    for s in data.get("symbols") or []:
        if s.get("symbol") not in want:
            continue
        f = {x.get("filterType"): x for x in (s.get("filters") or [])}
        lot = f.get("LOT_SIZE") or {}
        mkt = f.get("MARKET_LOT_SIZE") or {}
        mn = f.get("MIN_NOTIONAL") or {}
        print(f"  {s['symbol']:9s} status={s.get('status')} "
              f"stepSize={lot.get('stepSize')} minQty={lot.get('minQty')} "
              f"mktStep={mkt.get('stepSize')} minNotional={mn.get('notional')}")
except Exception as e:
    print("ERR:", e)
