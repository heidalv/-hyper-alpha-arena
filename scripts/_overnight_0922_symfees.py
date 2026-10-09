import json,pathlib,urllib.request
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
try:
    j=json.load(urllib.request.urlopen("https://fapi.asterdex.com/fapi/v1/exchangeInfo", timeout=30))
    for s in j["symbols"]:
        if s["symbol"] in ("ASTERUSDT","XRPUSDT","SOLUSDT","HYPEUSDT","BTCUSDT"):
            p(f"  {s['symbol']:<10} liquidationFee={s.get('liquidationFee')} maintMargin={s.get('maintMarginPercent')} reqMargin={s.get('requiredMarginPercent')} tags={s.get('tags')} subType={s.get('underlyingSubType')} symbolType={s.get('symbolType')} marketTakeBound={s.get('marketTakeBound')}")
except Exception as e:
    p("err:", e)
pathlib.Path("logs/_tmp_timeline/symfees.txt").write_text("\n".join(OUT), encoding="utf-8")
print("\n".join(OUT))
