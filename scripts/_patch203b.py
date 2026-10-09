import pathlib, re
p = pathlib.Path("scripts/_probe203_mm_trend_attribution.py")
s = p.read_text(encoding="utf-8")
# 用仓内封装取 K 线（避免猜列名：crypto_klines 实际是 close_price/period 等）
s = s.replace('''with market_engine.connect() as c:
    k = c.execute(text(
        "select timestamp, close_price from crypto_klines where symbol='BTC' and timeframe='1h' "
        "order by timestamp desc limit 20")).fetchall()
k = list(reversed(k))''',
'''from backend.services.agent_deep_context import _fetch_klines_for_prompt  # noqa: E402

_k = _fetch_klines_for_prompt("BTC", "1h", 24) or []
k = [(r.get("timestamp"), r.get("close")) for r in _k]''')
s = s.replace('''btc_ret: dict = {}
with market_engine.connect() as mc:
    kk = list(reversed(mc.execute(text(
        "select timestamp, close_price from crypto_klines where symbol='BTC' and timeframe='1h' "
        "order by timestamp desc limit 30")).fetchall()))''',
'''btc_ret: dict = {}
kk = [(r.get("timestamp"), r.get("close"))
      for r in (_fetch_klines_for_prompt("BTC", "1h", 40) or [])]''')
p.write_text(s, encoding="utf-8")
print("patched to use _fetch_klines_for_prompt")
