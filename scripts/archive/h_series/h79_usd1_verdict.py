"""H79：USD1 路线的可行性判定 —— 费用优势 vs 成交量/深度损失。

# H78 实测（Aster 官方 API，2026-09-21）

| 合约 | 24h额 | 价差 p50 | 买1量 USD | 卖1量 USD |
|---|---|---|---|---|
| XAUUSD1 | $234.8M | **0.0229bp** | 26,180 | 5,603 |
| CLUSD1 | $60.7M | 1.0257bp | 10,101 | 16,921 |
| MUUSD1 | $37.9M | 3.8728bp | 4,098 | 4,099 |
| BTCUSD1 | $3.7M | 1.2093bp | 648 | 10,130 |
| ETHUSD1 | $5.2M | 0.7616bp | 18,888 | **26** |
| SOLUSD1 | $1.6M | 1.8273bp | 7,083 | 7,127 |

# 这个表把子代理的结论**反过来了一半**

子代理说"USD1 盘口很深（XAUUSD1 $234.8M）"—— 成交量确实大，
**但价差只有 0.0229bp** ⇒ 那是一个**专业机构主导的极窄价差簿**，
做市在里面**没有价差可赚**（我们的收入 ∝ 价差）。

**⇒ "成交额大"和"价差可赚"是两件相反的事。** 这正是我 H70 发现的同一个陷阱
（评分函数偏爱宽价差低吞吐，而真实收入 ∝ 价差×笔数）。

# 本脚本算什么

对每个候选合约，用**统一口径**估单位时间毛收入与净成本：

    毛收入/小时 ≈ (spread_mult × 半价差) × 成交笔数/小时
    强平成本/小时 ≈ 强平率 × taker_fee × 笔数/小时
    净 ≈ 毛收入 − 强平成本

并**对照当前标的（USDS-M，taker 4bp）**。

判据（事先定死）：
  · 若 USD1 加密合约的**单位时间净收入** < 当前 USDS-M 标的 ⇒ **不迁移**
  · 若 > ⇒ 迁移值得做（但仍需先做小规模影子验证）

用法：
    .venv\\Scripts\\python.exe scripts\\h79_usd1_verdict.py
"""
from __future__ import annotations

import json
import ssl
import urllib.request

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
BASE = "https://fapi.asterdex.com"


def get(path, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}{path}" + (f"?{q}" if q else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
        return json.loads(r.read().decode())


def main():
    print("=" * 104)
    print("H79  USD1 路线判定：费用优势 vs 成交量/深度损失")
    print("=" * 104)

    t24 = {t["symbol"]: t for t in get("/fapi/v1/ticker/24hr")}
    # 拿成交笔数（/fapi/v1/klines 1m 的 count 字段 = 成交笔数）
    def trades_per_hour(sym, minutes=60):
        try:
            k = get("/fapi/v1/klines", symbol=sym, interval="1m", limit=minutes)
            return sum(int(r[8]) for r in k) / (len(k) / 60.0) if k else 0.0
        except Exception:
            return None

    def spread_now(sym):
        try:
            ob = get("/fapi/v1/depth", symbol=sym, limit=5)
            bb = float(ob["bids"][0][0]); ba = float(ob["asks"][0][0])
            mid = 0.5 * (bb + ba)
            return (ba - bb) / mid * 1e4
        except Exception:
            return None

    CAND = [
        # (合约, taker bp, 备注)
        ("BTCUSD1", 0.5, "USD1"),
        ("ETHUSD1", 0.5, "USD1"),
        ("SOLUSD1", 0.5, "USD1"),
        ("CLUSD1", 0.5, "USD1 商品"),
        ("SPCXUSD1", 0.5, "USD1 股票"),
        ("BTCUSDT", 4.0, "USDS-M 当前"),
        ("ETHUSDT", 4.0, "USDS-M 当前"),
        ("SOLUSDT", 4.0, "USDS-M 当前"),
        ("XRPUSDT", 4.0, "USDS-M 当前"),
        ("DOGEUSDT", 4.0, "USDS-M 当前"),
    ]
    SPREAD_MULT = 0.9

    print(f"\n  {'标的':<12} {'类型':<12} {'24h额$M':>9} {'价差bp':>8} "
          f"{'笔/小时':>9} {'毛收入bp/h':>11} {'taker':>6} {'净bp/h':>10}")
    print("  " + "-" * 92)
    out = []
    for sym, taker, kind in CAND:
        if sym not in t24:
            print(f"  {sym:<12} {kind:<12}  （合约不存在）")
            continue
        vol = float(t24[sym].get("quoteVolume") or 0) / 1e6
        sp = spread_now(sym)
        tph = trades_per_hour(sym)
        if sp is None or tph is None:
            print(f"  {sym:<12} {kind:<12} {vol:>9,.1f}  （数据缺失）")
            continue
        # 毛收入：每笔捕获 spread_mult × 半价差
        gross_bp_per_trade = SPREAD_MULT * sp / 2.0
        # 只有一部分笔数会被我们成交（保守取 10%，与当前实盘挂单占比同量级）
        FILL_SHARE = 0.10
        our_fills = tph * FILL_SHARE
        gross_bp_h = gross_bp_per_trade * our_fills
        # 强平成本：13% 的仓位强平，每次付 taker
        forced_bp_h = 0.13 * taker * our_fills
        net = gross_bp_h - forced_bp_h
        out.append({"sym": sym, "kind": kind, "vol_m": vol, "spread": sp,
                    "tph": tph, "gross": gross_bp_h, "taker": taker, "net": net})
        print(f"  {sym:<12} {kind:<12} {vol:>9,.1f} {sp:>8.4f} {tph:>9,.0f} "
              f"{gross_bp_h:>11.1f} {taker:>6.1f} {net:>+10.1f}")

    print("\n" + "=" * 104)
    print("判据（单位时间净收入，口径统一；FILL_SHARE=10% 对两边相同 ⇒ 不影响排序）")
    print("=" * 104)
    usd1 = [r for r in out if r["kind"].startswith("USD1")]
    base = [r for r in out if r["kind"].startswith("USDS")]
    if usd1 and base:
        bu = max(usd1, key=lambda x: x["net"])
        bb = max(base, key=lambda x: x["net"])
        print(f"\n  最佳 USD1: {bu['sym']:<12} 净 {bu['net']:>+9.1f} bp/h"
              f"  （价差 {bu['spread']:.4f}bp，{bu['tph']:,.0f} 笔/h）")
        print(f"  最佳 USDS-M: {bb['sym']:<12} 净 {bb['net']:>+9.1f} bp/h"
              f"  （价差 {bb['spread']:.4f}bp，{bb['tph']:,.0f} 笔/h）")
        if bu["net"] > bb["net"] * 1.2:
            print(f"\n  ⇒ **USD1 明显更优**（{bu['net']/max(bb['net'],1e-9):.2f}x）"
                  f" ⇒ 迁移值得做")
        elif bu["net"] > bb["net"]:
            print(f"\n  ⇒ USD1 略优但幅度有限 ⇒ 不足以抵消迁移风险，先做影子验证")
        else:
            print(f"\n  ⇒ **USD1 并不更优** ⇒ 不迁移")
            print("     原因：费率优势被价差/成交量劣势抵消")
            print("     ⚠️ 特别注意 XAUUSD1：成交额最大但价差仅 0.0229bp")
            print("        —— 「成交额大」与「价差可赚」是**相反**的两件事")

    print("\n" + "=" * 104)
    print("另有两条来自调研、必须记录的结论")
    print("=" * 104)
    print("  1. **只有两个场地有无门槛负 maker 费，且都不值得做**：")
    print("     · Hotstuff −0.2bp，但盘口仅 ~$300 深、OI 0.104 BTC；")
    print("       公开的做市 bot 实测两周 −$158.52（返佣只 +$12.81）")
    print("     · GRVT −0.01bp（可忽略）且 taker 4.5bp ⇒ 比不做还差")
    print("  2. **Aster $600M/14d 是顶档、不可达**（我们 $300 本金），")
    print("     且申请还要求 ≥$100M 月成交额；KPI 含最小报价量、价差 ≤0.1%/0.25%、")
    print("     ≥70% UTC 日合格。⇒ 返佣路线**确实关闭**（子代理证实了我的判断）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
