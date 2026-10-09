import time, urllib.request, statistics
from concurrent.futures import ThreadPoolExecutor
PROXY = "http://127.0.0.1:18080"
URL = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2"
op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))

def one(_):
    t0 = time.time()
    try:
        with op.open(URL, timeout=6) as r:   # 与 KLINE_P0 的 6s 单请求超时同口径
            r.read(64)
        return ("ok", (time.time() - t0) * 1000)
    except Exception as e:
        return (type(e).__name__, (time.time() - t0) * 1000)

def batch(n):
    with ThreadPoolExecutor(max_workers=n) as ex:
        res = list(ex.map(one, range(n)))
    ok = [d for k, d in res if k == "ok"]
    to = [k for k, _ in res if k != "ok"]
    p50 = statistics.median(ok) if ok else None
    print(f"  并发={n:2d}  成功={len(ok):2d}/{n}  超时/错={len(to):2d}  "
          f"延迟中位={('%.0f ms' % p50) if p50 else '—'}  最大={('%.0f ms' % max(ok)) if ok else '—'}")
    if to: print(f"           失败类型: {set(to)}")

print("=== 经代理并发压测（每批 1 轮，6s 单请求超时）===")
batch(8)
batch(32)
