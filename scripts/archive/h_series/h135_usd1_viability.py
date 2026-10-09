# -*- coding: utf-8 -*-
"""[H135 2026-09-21] 量化「换到 SOLUSD1」的收益 —— 以及必须先排除的流动性陷阱。

# 数学

我们的成本结构（H105/H124 实测）：
  · 每周期强平成本 ≈ **10.67bp**，其中 **taker 费 4.36bp**（含滑点口径）
  · SOL 实测：强平率 **7.0%**、每周期净额 **+0.0471bp**（USDT 版，几乎打平）

换到 `SOLUSD1`（taker **0.5bp**）后：
  · taker 费 4.36bp → 约 **0.86bp**（含同等滑点口径）⇒ 每周期节省 3.5bp × 强平率
  · 但 USD1 合约的**点差/流动性可能完全不同** ⇒ 必须先量，不能假设

# 必须先排除的陷阱（三条，任一条不成立则整个推论作废）

  T1 **流动性**：USD1 书的 24h 成交额如果只有 USDT 书的百分之几，
     那"省下的 taker 费"会被"更宽的挂单点差 + 更差的成交概率"吃掉。
  T2 **点差**：我们赚的是点差。若 SOLUSD1 点差远宽于 0.89bp，
     说明流动性差（不是机会），若远窄于 0.8bp，往返净为负。
  T3 **数据可得性**：引擎要报价必须有该 symbol 的 book_ticker + 20 档深度。
     我们的采集列表是硬编码的 ⇒ 没采就没法做。

用法：
    .venv\\Scripts\\python.exe scripts\\h135_usd1_viability.py
"""
from __future__ import annotations

from pathlib import Path

import psycopg
import requests

ROOT = Path(__file__).resolve().parents[1]
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"}
FAPI = "https://fapi.asterdex.com"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def api(path: str):
    try:
        r = requests.get(FAPI + path, headers=H, timeout=20)
        if r.status_code == 200:
            return r.json()
        print(f"    {path} -> HTTP {r.status_code}")
    except Exception as e:
        print(f"    {path} -> {type(e).__name__}")
    return None


def main() -> int:
    pairs = [("SOLUSDT", "SOLUSD1"), ("BTCUSDT", "BTCUSD1"), ("ETHUSDT", "ETHUSD1")]

    print("=" * 100)
    print("H135  换到 USD1 合约（taker 0.5bp）的可行性 —— 先排流动性陷阱")
    print("=" * 100)

    # ── T1/T2：24h 成交额 + 点差 ─────────────────────────────
    print("\n【T1/T2】24h 成交额与点差对比")
    tick = api("/fapi/v1/ticker/24hr") or []
    bysym = {str(t.get("symbol")): t for t in tick}
    print(f"  {'symbol':<12} {'24h成交额USD':>16} {'笔数':>10} {'最新价':>14}")
    print("  " + "-" * 58)
    for base, u1 in pairs:
        for s in (base, u1):
            t = bysym.get(s)
            if not t:
                print(f"  {s:<12} {'(ticker 无此符号)':>16}")
                continue
            print(f"  {s:<12} {float(t.get('quoteVolume') or 0):>16,.0f} "
                  f"{int(t.get('count') or 0):>10,} {float(t.get('lastPrice') or 0):>14.6f}")

    print("\n【T2】盘口点差（现价一手）")
    print(f"  {'symbol':<12} {'bid':>14} {'ask':>14} {'点差bp':>8}")
    print("  " + "-" * 54)
    for base, u1 in pairs:
        for s in (base, u1):
            ob = api(f"/fapi/v1/depth?symbol={s}&limit=5")
            if not ob or not ob.get("bids") or not ob.get("asks"):
                print(f"  {s:<12} {'(无盘口)':>14}")
                continue
            b = float(ob["bids"][0][0])
            a = float(ob["asks"][0][0])
            mid = (a + b) / 2
            print(f"  {s:<12} {b:>14.6f} {a:>14.6f} {(a-b)/mid*1e4:>8.3f}")

    # ── 收益量化 ─────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("收益量化：taker 4.36bp → 0.86bp 对 SOL 每周期净额的影响")
    print("=" * 100)
    sol_net_bp = 0.0471        # H124 实测（USDT 版）
    sol_fr = 0.070             # H124 实测强平率
    save = (4.36 - 0.86) * sol_fr
    print(f"  SOL 实测（USDT 版）：每周期净额 {sol_net_bp:+.4f}bp，强平率 {sol_fr*100:.1f}%")
    print(f"  换 SOLUSD1 后每周期节省 = (4.36 − 0.86)bp × {sol_fr*100:.1f}% = **{save:+.4f}bp**")
    print(f"  ⇒ 新每周期净额 ≈ **{sol_net_bp + save:+.4f}bp**"
          f"（{sol_net_bp:+.4f} → {sol_net_bp + save:+.4f}，约 {(sol_net_bp + save)/max(sol_net_bp,1e-9):.1f} 倍）")
    print(f"\n  ⚠️ 这 +{save:.4f}bp 只在「SOLUSD1 的点差与成交概率和 SOLUSDT 相当」时才成立。")
    print(f"     若 USD1 书点差宽 3 倍，我们赚的捕获也会变（但捕获率实测只有 28%），")
    print(f"     而挂单成交概率下降会**直接减少周期数** ⇒ 必须看上面的实测点差再决定。")

    # ── T3：数据可得性 ───────────────────────────────────────
    print("\n" + "=" * 100)
    print("【T3】数据可得性（引擎要报价必须有 book_ticker + 20 档深度）")
    print("=" * 100)
    murl = dsn().rsplit("/", 1)[0] + "/alpha_market"
    with psycopg.connect(murl) as c:
        with c.cursor() as cur:
            for s in ("SOLUSDT", "SOLUSD1", "BTCUSD1", "ETHUSD1"):
                cur.execute("SELECT count(*), max(event_ts_ms) FROM asterdex_book_ticker"
                            " WHERE symbol=%s AND event_ts_ms >= "
                            " (extract(epoch from now())-3600)*1000", (s,))
                n, last = cur.fetchone()
                cur.execute("SELECT count(*) FROM asterdex_depth_snapshots"
                            " WHERE symbol=%s AND event_ts_ms >= "
                            " (extract(epoch from now())-3600)*1000", (s,))
                d = cur.fetchone()[0]
                print(f"  {s:<10} 近1h 盘口 {n:>9,} 行   20档深度 {d:>6,} 行")
    print("\n  ⇒ 若 SOLUSD1 两行都是 0，则引擎**无法**为它报价：需要先把它加进")
    print("     `services/aster_ws_ingest.py --symbols/--depth-symbols` 的采集列表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
