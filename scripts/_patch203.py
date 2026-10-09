import pathlib
p = pathlib.Path("scripts/_probe203_mm_trend_attribution.py")
s = p.read_text(encoding="utf-8")
s = s.replace("select timestamp, close from crypto_klines", "select timestamp, close_price from crypto_klines")
s = s.replace("for ts, close in k:\n    if prev:\n        ret = (float(close) / float(prev) - 1) * 100",
              "for ts, close in k:\n    if prev:\n        ret = (float(close) / float(prev) - 1) * 100")
s = s.replace("    prev = close", "    prev = close")
p.write_text(s, encoding="utf-8")
print("patched close→close_price")
