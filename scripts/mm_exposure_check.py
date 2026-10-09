import json, urllib.request

BASE = "http://127.0.0.1:8000"


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=120) as r:
        return json.load(r)


lanes = get("/api/trading/lanes")
mm = next(x for x in lanes["items"] if x["lane_id"] == "mm_asterdex")
risk = mm.get("risk") or {}
params = (mm.get("meta") or {}).get("params") or {}
print("== 车道 risk 块（前端面板读数来源）==")
print("  ", json.dumps(risk, ensure_ascii=False))
print("== 部署 params（runner 真正读的那份）==")
for k in ("max_net_exposure_ratio", "max_net_directional_ratio",
          "max_gross_notional_ratio", "max_symbol_notional_ratio",
          "daily_loss_stop_pct", "vol_pause_sigma"):
    print(f"   {k} = {params.get(k, '(未设置 → 用 dataclass 默认)')}")

from dataclasses import fields
import sys
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
from backend.services.market_maker.core import LaneRiskLimits
names = {f.name for f in fields(LaneRiskLimits)}
print("\n== LaneRiskLimits 是否有 max_net_exposure_pct 字段？ ==")
print("   max_net_exposure_pct in fields:", "max_net_exposure_pct" in names)
print("   max_symbol_exposure_pct in fields:", "max_symbol_exposure_pct" in names)

rs = get("/api/trading/risk/summary")
print("\n== /risk/summary ==")
for k in ("equity", "gross_exposure_usd", "net_exposure_usd", "net_exposure_pct",
          "unrealized_usd", "max_symbol", "max_symbol_exposure_pct", "limits"):
    print(f"   {k} = {rs.get(k)}")

pos = get("/api/trading/positions")
print("\n== /positions ==")
print("   count =", pos.get("count"), " open =", pos.get("open_count"),
      " gross =", pos.get("gross_exposure_usd"), " net =", pos.get("net_exposure_usd"),
      " unrealized =", pos.get("unrealized_usd"))
for it in (pos.get("items") or []):
    if abs(it.get("qty") or 0) > 1e-12:
        print("   ", json.dumps(it, ensure_ascii=False, default=str)[:260])

sh = get("/api/trading/lanes/mm_asterdex/shadow")
print("\n== 车道实时 ==")
for s, v in (sh.get("states") or {}).items():
    q = float(v.get("qty") or 0)
    if abs(q) > 0:
        px = float(v.get("avg_px") or 0)
        print(f"   {s}: qty={q} avg_px={px:.4f} 名义≈${abs(q) * px:.2f}")
print("   equity =", sh.get("equity"), " fill_notional =", sh.get("fill_notional"))
