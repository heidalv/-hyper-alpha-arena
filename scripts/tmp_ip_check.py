import httpx
print("=== 代理出口 IP（走 1080）===")
try:
    r = httpx.get("https://api.ip.sb/ip", timeout=15, proxy="http://127.0.0.1:1080")
    print("代理出口:", r.text.strip())
except Exception as e:
    print("ip.sb FAIL:", e)
    try:
        r = httpx.get("https://httpbin.org/ip", timeout=15, proxy="http://127.0.0.1:1080")
        print("代理出口(httpbin):", r.text.strip())
    except Exception as e2:
        print("httpbin FAIL:", e2)
try:
    r = httpx.get("https://api.ipify.org", timeout=10, proxy=None)
    print("直连出口:", r.text.strip())
except Exception as e:
    print("直连ipify FAIL:", e)
