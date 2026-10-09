# -*- coding: utf-8 -*-
"""验证 /api/hft/account 的账户总账块（对账恒等式）。"""
import sys
import json
import urllib.request

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

for port in (8000, 8080, 8888):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hft/account", timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
        paper = d.get("paper") or {}
        b = paper.get("book")
        print(f"端口 {port} ✓")
        print("  realized_usd(时代) =", paper.get("realized_usd"))
        print("  fee_usd(时代)      =", paper.get("fee_usd"))
        print("  fee_lifetime_usd   =", paper.get("fee_lifetime_usd"))
        if not b:
            print("  ⚠ book 块为空（热重载未生效或查询失败）")
        else:
            print("  == 账户总账（不可清零口径）==")
            print(f"    起始        = {b['initial_usd']:+.4f}")
            print(f"    外部划拨    = {b['capital_adj_usd']:+.4f}")
            print(f"    累计已实现  = {b['realized_all_usd']:+.4f}")
            print(f"    累计手续费  = {b['fee_all_usd']:+.4f}")
            print(f"    = 当前权益  = {b['equity_usd']:+.4f}")
            print(f"    对账残差    = {b['recon_residual_usd']:+.6f}"
                  f"  {'✓ 自洽' if abs(b['recon_residual_usd']) < 0.01 else '✗ 脱钩'}")
        break
    except Exception as e:
        print(f"端口 {port}: {e}")
