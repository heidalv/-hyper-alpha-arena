"""快速查看 /api/hft/* 的关键返回值（避免 PowerShell 内联转义反复踩坑）。"""
from __future__ import annotations

import json
import sys
import urllib.request


def get(ep: str):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{ep}", timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    d = get("/api/hft/fills?limit=12&hours=24")
    s = d["summary"]
    print(f"近 {d['hours']}h  成交 {s['fills']} 笔  "
          f"名义 {s['notional_sum']:.2f} USD  净 {s['net_usd_sum']:+.4f} USD")
    print(f"六维合计 bp: spread={s['spread_bp_sum']:+.2f}  price={s['price_bp_sum']:+.2f}  "
          f"fee={s['fee_bp_sum']:+.2f}  net={s['net_bp_sum']:+.2f}")
    print()
    if not d["items"]:
        print("  （该区间无成交）")
    for it in d["items"]:
        ts = (it["ts"] or "")[:19]
        print(f"  {ts}  {it['symbol']:<10} notional={it['notional_usd']:>7.2f}  "
              f"spread={it['spread_bp']:>+8.2f}  price={it['price_bp']:>+9.2f}  "
              f"fee={it['fee_bp']:>+6.2f}  net={it['net_bp']:>+9.2f}bp  "
              f"net={it['net_usd']:>+8.4f}$")
    return 0


if __name__ == "__main__":
    sys.exit(main())
