import httpx, time
P = "http://127.0.0.1:1080"
ok = 0
for name, url in [
    ("ping", "https://fapi.asterdex.com/fapi/v1/ping"),
    ("ticker/BTC", "https://fapi.asterdex.com/fapi/v1/ticker/24hr?symbol=BTCUSDT"),
    ("klines", "https://fapi.asterdex.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=10"),
    ("premiumIndex", "https://fapi.asterdex.com/fapi/v1/premiumIndex"),
]:
    try:
        r = httpx.get(url, timeout=15, proxy=P)
        print(f"[{name}] {r.status_code} {len(r.content)}B")
        ok += 1 if r.status_code == 200 else 0
    except Exception as e:
        print(f"[{name}] FAIL {type(e).__name__}: {str(e)[:90]}")
print(f"成功率 {ok}/4")
