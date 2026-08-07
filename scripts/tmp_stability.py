import httpx, time, json
P = "http://127.0.0.1:1080"
BASE = "https://fapi.asterdex.com"

def test(name, url, n=3, timeout=15):
    ok = 0
    for i in range(n):
        try:
            t0 = time.time()
            r = httpx.get(url, timeout=timeout, proxy=P)
            size = len(r.content)
            print(f"  [{name}#{i+1}] {r.status_code} {time.time()-t0:.2f}s size={size}B")
            ok += 1
        except Exception as e:
            print(f"  [{name}#{i+1}] FAIL {type(e).__name__}: {str(e)[:80]}")
    print(f"  => {name} 成功率 {ok}/{n}")
    return ok

print("=== ticker/24hr 单symbol 重试3次 ===")
test("ticker24h", f"{BASE}/fapi/v1/ticker/24hr?symbol=BTCUSDT")

print("=== klines 重试3次 ===")
test("klines", f"{BASE}/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=10")

print("=== depth 重试3次 ===")
test("depth", f"{BASE}/fapi/v1/depth?symbol=BTCUSDT&limit=20")

print("=== premiumIndex 重试3次 ===")
test("premiumIndex", f"{BASE}/fapi/v1/premiumIndex")

print("=== 全市场ticker（大响应）1次 ===")
test("tickerALL", f"{BASE}/fapi/v1/ticker/24hr", n=1, timeout=30)
