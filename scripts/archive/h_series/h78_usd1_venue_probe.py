"""H78：Aster 的 USD1 合约能不能做 —— taker 4bp → 0.5bp 是 +0.455bp/仓位。

# 为什么查这个（子代理调研的核心发现）

Aster 的 **USD1 General Perps 用 0.5bp taker**（USDⓈ-M 是 4bp），maker 都是 0。
而我的实测成本结构里，**强平腿 −12.73bp 中有 ~4bp 就是 Aster 的 taker 费**：

    强平成本 = 穿越半价差 ~1.8bp + **taker 4bp** + 持仓不利移动 ~6.9bp
    强平率 13% ⇒ taker 从 4bp 降到 0.5bp 值 **+0.455bp/仓位**

**而我们当前每笔只有 −0.23bp。** 所以这一项单独就能把它翻正。

# 但必须先验证三件事（否则就是又一次"听起来很好"）

1. **合约真的存在且在交易** —— 已用 `/fapi/v1/exchangeInfo` 确认 12 个 USD1 合约 TRADING ✓
2. **盘口真有深度** —— 子代理报 XAUUSD1 $234.8M/24h，但 **BTCUSD1 只有 $3.7M**
3. **价差是否够宽** —— 做市要有价差可赚；若价差极窄，taker 省下的钱也赚不回来

判据（事先定死）：
  · 价差 p50 ≥ 0.8bp（我们自己的 MIN_SPREAD_BP 门槛）**且** 24h 成交额 ≥ $5M
    ⇒ 该合约可行
  · 若 BTCUSD1/ETHUSD1 的价差或深度不达标 ⇒ USD1 路线对我们**不可用**
    （因为我们的策略只在加密标的上验证过；gold/crude 是另一套动力学）

用法：
    .venv\\Scripts\\python.exe scripts\\h78_usd1_venue_probe.py
"""
from __future__ import annotations

import json
import ssl
import statistics
import time
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
    print("=" * 100)
    print("H78  Aster USD1 合约可行性（taker 4bp → 0.5bp = +0.455bp/仓位）")
    print("=" * 100)

    # 1) 24h 成交额
    try:
        t24 = get("/fapi/v1/ticker/24hr")
    except Exception as e:
        print(f"  ✗ ticker/24hr 失败: {e}")
        return 1
    by = {t.get("symbol"): t for t in t24}
    u1 = [s for s in by if "USD1" in s]
    print(f"\n  24h ticker 拿到 {len(by)} 个合约，其中 USD1 系列 {len(u1)} 个")

    # 2) 逐个测价差与深度
    print(f"\n  {'合约':<18} {'24h额(USD)':>14} {'价差p50 bp':>11} {'价差p90':>9} "
          f"{'买1量USD':>12} {'卖1量USD':>12} {'样本':>6}")
    print("  " + "-" * 92)
    rows = []
    for s in sorted(u1):
        t = by[s]
        vol = float(t.get("quoteVolume") or 0)
        try:
            ob = get("/fapi/v1/depth", symbol=s, limit=5)
        except Exception as e:
            print(f"  {s:<18} {vol:>14,.0f}   depth 失败: {type(e).__name__}")
            continue
        bids = ob.get("bids") or []
        asks = ob.get("asks") or []
        if not bids or not asks:
            print(f"  {s:<18} {vol:>14,.0f}   **盘口为空**")
            continue
        bb, ba = float(bids[0][0]), float(asks[0][0])
        bq, aq = float(bids[0][1]), float(asks[0][1])
        mid = 0.5 * (bb + ba)
        sp = (ba - bb) / mid * 1e4 if mid > 0 else float("nan")
        rows.append({"sym": s, "vol": vol, "spread_bp": sp,
                     "bid_usd": bq * bb, "ask_usd": aq * ba})
        print(f"  {s:<18} {vol:>14,.0f} {sp:>11.4f} {'':>9} "
              f"{bq*bb:>12,.0f} {aq*ba:>12,.0f} {len(bids):>6}")

    # 3) 对照：加密 USDⓈ-M 的价差
    print(f"\n  ── 对照：USDⓈ-M（当前在用的，taker 4bp）──")
    print(f"  {'合约':<18} {'24h额(USD)':>14} {'价差p50 bp':>11} {'买1量USD':>12} {'卖1量USD':>12}")
    print("  " + "-" * 72)
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"):
        if s not in by:
            continue
        t = by[s]
        vol = float(t.get("quoteVolume") or 0)
        try:
            ob = get("/fapi/v1/depth", symbol=s, limit=5)
            bids = ob.get("bids") or []; asks = ob.get("asks") or []
            if not bids or not asks:
                continue
            bb, ba = float(bids[0][0]), float(asks[0][0])
            bq, aq = float(bids[0][1]), float(asks[0][1])
            mid = 0.5 * (bb + ba)
            sp = (ba - bb) / mid * 1e4
            print(f"  {s:<18} {vol:>14,.0f} {sp:>11.4f} {bq*bb:>12,.0f} {aq*ba:>12,.0f}")
        except Exception as e:
            print(f"  {s:<18} {vol:>14,.0f}   失败 {type(e).__name__}")

    # 4) 判据
    print("\n" + "=" * 100)
    print("判据（事先定死：价差 p50 ≥ 0.8bp 且 24h 额 ≥ $5M ⇒ 可行）")
    print("=" * 100)
    ok = [r for r in rows if r["spread_bp"] >= 0.8 and r["vol"] >= 5e6]
    thin = [r for r in rows if r["vol"] < 5e6]
    narrow = [r for r in rows if r["spread_bp"] < 0.8]
    print(f"\n  可行（价差≥0.8bp 且 额≥$5M）: {len(ok)} 个")
    for r in sorted(ok, key=lambda x: -x["vol"]):
        print(f"    {r['sym']:<18} 额 ${r['vol']/1e6:>8.1f}M  价差 {r['spread_bp']:.4f}bp")
    print(f"\n  成交额不足 $5M: {len(thin)} 个  |  价差 < 0.8bp: {len(narrow)} 个")

    # 关键：BTC/ETH 的 USD1 合约是否可用
    print(f"\n  ── 对我们最关键的两个 ──")
    for s in ("BTCUSD1", "ETHUSD1", "SOLUSD1"):
        r = next((x for x in rows if x["sym"] == s), None)
        if r:
            verdict = "可行" if (r["spread_bp"] >= 0.8 and r["vol"] >= 5e6) else "**不可行**"
            why = []
            if r["vol"] < 5e6:
                why.append(f"额仅 ${r['vol']/1e6:.1f}M")
            if r["spread_bp"] < 0.8:
                why.append(f"价差仅 {r['spread_bp']:.4f}bp")
            print(f"    {s:<12} {verdict}  {'; '.join(why)}")
    print("\n  注：我们仅在加密标的上验证过策略；gold/crude/equity 是另一套动力学，")
    print("      直接迁移属于**未验证假设**，不能当作已解决。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
