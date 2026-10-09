import json, time, urllib.request

BASE = "http://127.0.0.1:8000"


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=120) as r:
        return json.load(r)


sh = get("/api/trading/lanes/mm_asterdex/shadow")
print("ticks =", sh.get("ticks"), " fills =", sh.get("fills"),
      " last_tick_age_s =", round(time.time() - float(sh.get("last_tick_ts") or 0), 1))
print("equity/fill_notional =", sh.get("equity"), "/", sh.get("fill_notional"))
print("side_counts =", json.dumps(sh.get("side_counts"), default=str))
print("skip_counts =", json.dumps(sh.get("skip_counts"), default=str))
print("quote_modes =", json.dumps(sh.get("quote_modes"), default=str),
      " avg_width_bp =", json.dumps(sh.get("avg_width_bp"), default=str))
print("avg_sigma(挂单决策) =", sh.get("avg_sigma"),
      " avg_sigma_all(全部决策) =", sh.get("avg_sigma_all"),
      " sigma_decisions =", json.dumps(sh.get("sigma_decisions"), default=str))
print("qty =", json.dumps({k: v.get("qty") for k, v in (sh.get("states") or {}).items()}, default=str))
lanes = get("/api/trading/lanes")
mm = next(x for x in lanes["items"] if x["lane_id"] == "mm_asterdex")
print("params.daily_loss_stop_pct =", (mm["meta"].get("params") or {}).get("daily_loss_stop_pct"),
      " params.vol_pause_sigma =", (mm["meta"].get("params") or {}).get("vol_pause_sigma"))
ua = get("/api/trading/account/unified")
print("account =", json.dumps(ua["account"], ensure_ascii=False, default=str))
print("era =", json.dumps({k: v for k, v in ua["era"].items() if k != "strategies"}, ensure_ascii=False, default=str))
