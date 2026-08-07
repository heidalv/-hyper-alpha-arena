import httpx, os
os.environ["BINANCE_HTTPS_PROXY"] = "http://127.0.0.1:1080"
for k in ("HTTPS_PROXY","HTTP_PROXY","MARKET_DATA_HTTP_PROXY","BINANCE_HTTP_PROXY"):
    os.environ.pop(k, None)
try:
    r = httpx.get("https://fapi.asterdex.com/fapi/v1/ping", timeout=15, proxy="http://127.0.0.1:1080")
    print("限流状态:", r.status_code)
except Exception as e:
    print("仍失败:", type(e).__name__, str(e)[:100])
