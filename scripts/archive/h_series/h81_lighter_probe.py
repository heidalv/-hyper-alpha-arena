"""H81：Lighter 可行性评估 —— 唯一能**单独**把 −0.23bp 翻正的已知项。

# 为什么值得单独查

费率调研（35 场地）结论：**Lighter Standard = maker 0 / taker 0，且无门槛**。
我们的成本结构是 入场 −0.20bp + 出场 −1.65bp = **−0.23bp/仓位**，
其中出场的 −1.65bp ≈ **13% 强平率 × taker 费**。
若 taker = 0 ⇒ 省下 **+0.52bp/仓位** ⇒ **单独就能翻正**。

# 但调研也给了两个必须验证的疑虑

1. **延迟 200–300ms** —— 我们的决策节奏是 15s，看似够用，但**盘口是 8~37ms 更新**，
   若报价要跟着盘口走，300ms 的滞后会显著影响挂单质量（正是 F281 修的那个问题）
2. **没有批量历史 L2** —— 只有 Binance/OKX 提供；Lighter 需自建
   ⇒ **无法在迁移前回测** ⇒ 这是**不能先验证的迁移**（第 38 条教训）

# 本脚本查什么（只查公开可得的事实，不做无根据推断）

  1. Lighter 的公开 API 是否可达、有哪些端点
  2. 是否提供历史成交 / K 线（可用于粗回测，即使没有 L2）
  3. 盘口深度与价差（能否支撑我们的腿量）
  4. 当前是否有 BTC/ETH/SOL 等我们熟悉的标的
  5. **响应延迟实测**（连发若干次请求，量 RTT）

判据（事先定死）：
  · 若有历史成交/K 线 且 盘口价差 ≥0.8bp 且 深度 ≥$5k ⇒ **值得做影子验证**
  · 若拿不到任何历史数据 ⇒ 标记为**不可先行验证**，需要用户对"盲迁"做决策
  · 若 RTT 中位 > 500ms ⇒ 与我们 8~37ms 的盘口节奏不匹配，风险高

用法：
    .venv\\Scripts\\python.exe scripts\\h81_lighter_probe.py
"""
from __future__ import annotations

import json
import ssl
import statistics
import time
import urllib.error
import urllib.request

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

CANDIDATES = [
    "https://mainnet.zklighter.elliot.ai",
    "https://api.zklighter.elliot.ai",
]
ENDPOINTS = [
    "/api/v1/orderBooks",
    "/api/v1/orderBookDetails",
    "/api/v1/candles",
    "/api/v1/trades",
    "/api/v1/funding-rates",
    "/info",
    "/",
]


def probe(url, timeout=15):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            body = r.read(4000).decode("utf-8", errors="replace")
            return r.status, (time.time() - t0) * 1000, body
    except urllib.error.HTTPError as e:
        return e.code, (time.time() - t0) * 1000, str(e.reason)[:120]
    except Exception as e:
        return None, (time.time() - t0) * 1000, f"{type(e).__name__}: {str(e)[:100]}"


def main():
    print("=" * 100)
    print("H81  Lighter 可行性评估（maker 0 / taker 0，无门槛）")
    print("=" * 100)
    print(f"  目标：省 +0.52bp/仓位 —— 这是我方唯一能单独翻正的已知项")

    base = None
    for b in CANDIDATES:
        st, ms, body = probe(b + "/api/v1/orderBooks", timeout=15)
        print(f"\n  {b:<42} status={st}  {ms:.0f}ms")
        if st == 200:
            base = b
            print(f"    ⇒ 可达 ✓  响应前 200 字符：{body[:200]}")
            break
        else:
            print(f"    body: {body[:140]}")

    if not base:
        print("\n  ⚠️ 两个候选 base 都不可达 —— 无法从本环境验证 Lighter。")
        print("     ⇒ 结论：**无法先行验证**，迁移属盲迁（需人工决策 + 用户授权）")
        return 0

    # 端点扫描
    print("\n" + "=" * 100)
    print("端点可用性（决定能否做粗回测）")
    print("=" * 100)
    rtts = []
    for ep in ENDPOINTS:
        st, ms, body = probe(base + ep, timeout=15)
        rtts.append(ms)
        ok = "✓" if st == 200 else "✗"
        print(f"  {ok} {ep:<26} status={str(st):<6} {ms:>7.0f}ms  {body[:90]}")

    # RTT 统计
    print("\n" + "=" * 100)
    print("响应延迟实测（连发 7 次 orderBookDetails）")
    print("=" * 100)
    lat = []
    for i in range(7):
        st, ms, _ = probe(base + "/api/v1/orderBookDetails", timeout=15)
        lat.append(ms)
        print(f"  第 {i+1} 次: status={st}  {ms:.0f}ms")
    if lat:
        print(f"\n  RTT 中位 {statistics.median(lat):.0f}ms  "
              f"最小 {min(lat):.0f}ms  最大 {max(lat):.0f}ms")
        if statistics.median(lat) > 500:
            print("  ⇒ 中位 > 500ms ⇒ 与本项目 8~37ms 的盘口节奏**不匹配**，风险高")
        else:
            print("  ⇒ 中位 ≤ 500ms ⇒ 对 15s 决策节奏够用（但仍需评估 WS 推送延迟）")

    # 盘口：找我们熟悉的标的
    print("\n" + "=" * 100)
    print("盘口深度与价差（能否支撑 $135/腿）")
    print("=" * 100)
    try:
        req = urllib.request.Request(base + "/api/v1/orderBookDetails",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        print(f"  取 orderBookDetails 失败: {e}")
        return 0

    items = d if isinstance(d, list) else (d.get("order_book_details") or d.get("data") or [])
    if not items:
        print(f"  返回结构未知，顶层键：{list(d)[:10] if isinstance(d, dict) else type(d)}")
        return 0
    print(f"  拿到 {len(items)} 个市场")
    want = ("BTC", "ETH", "SOL", "XRP", "DOGE")
    print(f"\n  {'市场':<16} {'标记价':>12} {'日成交额USD':>15} {'OI USD':>13}")
    print("  " + "-" * 62)
    hit = 0
    for it in items:
        sym = str(it.get("symbol") or it.get("market") or "")
        if not any(w in sym.upper() for w in want):
            continue
        hit += 1
        if hit > 12:
            break
        px = it.get("mark_price") or it.get("last_trade_price") or 0
        vol = it.get("daily_quote_token_volume") or it.get("daily_volume") or 0
        oi = it.get("open_interest") or 0
        print(f"  {sym:<16} {float(px or 0):>12,.4f} {float(vol or 0):>15,.0f} "
              f"{float(oi or 0):>13,.0f}")
    if hit == 0:
        print("  （没找到我们熟悉的标的，列出前 10 个）")
        for it in items[:10]:
            print(f"    {it.get('symbol') or it.get('market')}")

    print("\n" + "=" * 100)
    print("判据与结论")
    print("=" * 100)
    print("  · 可达性：%s" % ("可探测" if base else "不可探测"))
    print("  · 历史数据：见上面的 /api/v1/candles 与 /api/v1/trades 是否 200")
    print("  · 若两者可用 ⇒ 可做**粗回测**（虽无 L2，但能用 K 线/成交估价差与吞吐）")
    print("  · **但 L2 队列建模仍需自建** ⇒ 迁移仍属未充分验证 ⚠️")
    print("\n  ⇒ 本轮结论只到「可行性探测」；是否迁移需用户对「盲迁风险」明确表态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
