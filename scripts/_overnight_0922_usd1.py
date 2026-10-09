import json,pathlib,urllib.request
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
j=json.load(urllib.request.urlopen("https://fapi.asterdex.com/fapi/v1/exchangeInfo", timeout=40))
usd1=[s for s in j["symbols"] if s.get("quoteAsset")=="USD1"]
p(f"USD1 计价永续共 {len(usd1)} 个（TRADING）:")
for s in usd1:
    if s.get("status")=="TRADING":
        p(f"   {s['symbol']:<14} base={s.get('baseAsset'):<8} subType={s.get('underlyingSubType')} maint={s.get('maintMarginPercent')} liqFee={s.get('liquidationFee')}")
p("\n== 24h 成交额对比（USDT 对 vs USD1 对） ==")
try:
    t=json.load(urllib.request.urlopen("https://fapi.asterdex.com/fapi/v1/ticker/24hr", timeout=40))
    d={x["symbol"]:x for x in t}
    for sym in ("SOLUSDT","SOLUSD1","BTCUSDT","BTCUSD1","ETHUSDT","ETHUSD1","XRPUSDT","XRPUSD1","HYPEUSDT","HYPEUSD1","ASTERUSDT","ASTERUSD1"):
        x=d.get(sym)
        if not x: p(f"   {sym:<12} 不存在"); continue
        p(f"   {sym:<12} quoteVol={float(x['quoteVolume']):>14,.0f}  lastPx={x['lastPrice']:>12}  trades={x['count']:>8}")
except Exception as e:
    p("  ticker err:", e)
pathlib.Path("logs/_tmp_timeline/usd1.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
