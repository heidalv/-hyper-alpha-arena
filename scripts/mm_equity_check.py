import json, time, urllib.request

BASE = "http://127.0.0.1:8000"


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=120) as r:
        return json.load(r)


ps = get("/api/trading/portfolio/summary")
ua = get("/api/trading/account/unified")
print("portfolio/summary equity =", ps.get("equity"), " source =", ps.get("equity_source"))
print("account.equity           =", ua["account"]["equity"],
      " (total_equity", ua["account"]["total_equity"], "+ realized",
      ua["account"]["realized_pnl"], ")")
print("era                      =", json.dumps(
    {k: v for k, v in ua["era"].items() if k != "strategies"}, ensure_ascii=False, default=str))
sh = get("/api/trading/lanes/mm_asterdex/shadow")
print("lane ticks/fills         =", sh.get("ticks"), "/", sh.get("fills"),
      " equity(self) =", sh.get("equity"))
print("一致？", ps.get("equity") == ua["account"]["equity"])
