# -*- coding: utf-8 -*-
"""[H173 2026-09-21] 出库策略的**完整全规则模拟** —— 三个方案在同一批入场上的真实期望。

# 为什么必须重做（H172 的问题）

H172 发现了一个我先前没看清的结构冲突：

    出库单赚钱 = r × 半价差（**绝对价格偏移**，实测约 0.2~2bp）
    止盈要的是 +12bp
    ⇒ 出库单**永远先在更低的位置成交** ⇒ 止盈占比恒为 0.0%

**两条出库路径在同一个"有利偏移"轴上竞争，而出库单总在更近处。**
所以"扩大出库挂宽"只是让被动出库多赚一点，**并不能让止盈生效**。

H172 还得出了"r=2.0 最优"的结论，但那个脚本**没有把未触发样本的成本算进去**
（它把"未出库"记为 0 收益，而现实里那些仓位会继续持有、最终被止损或超时）。
⇒ 结论不可用。

# 本脚本：对同一批入场，完整模拟三个方案的**全部成本**

方案 A（现状）：被动出库 r=0.4，止盈 12bp（实际上被出库单压制）
方案 B（纯止盈）：**不挂减仓侧出库单**，只靠 ① 止盈 12bp ② 止损 40bp ③ 超时 300s 后 taker
方案 C（折中）：出库单挂到 r=1.5（几乎不成交），止盈 12bp，止损 40bp

对每个入场，用真实盘口按时间顺序推进，**先发生者胜**：
  · 有利偏移 ≥ tp        ⇒ 止盈成交：净 `+tp − 4bp`
  · 不利偏移 ≥ sl        ⇒ 止损成交：净 `−sl − 4bp`
  · 有利偏移 ≥ r×半价差  ⇒ 被动出库（仅 A/C）：净 `+r×半价差`（maker 免费）
  · 时间超过 300s        ⇒ 超时：按当时的**浮动盈亏**结算，再付 4bp
  · 到 horizon 仍未结束  ⇒ 按当时的浮动盈亏结算（不付费，代表"还开着"）

# 判据

  · 取**整体每笔净 bp** 最高者
  · 同时给出三条路径的占比 —— 看"止盈是否真的成为了主要出库路径"
  · 与现状基准 +0.1374bp 对比

用法：
    .venv\\Scripts\\python.exe scripts\\h173_full_exit_sim.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
TAKER_BP = 4.0


def dsn(db: str = "alpha_arena") -> str:
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
    return f"{url.rsplit('/', 1)[0]}/{db}"


def load_sequences(entries, horizon):
    """一次性把每笔入场后的盘口序列取出来（避免重复查询）。"""
    out = []
    for (rid, ts, sym, side) in entries:
        t0 = ts.timestamp()
        with psycopg.connect(dsn("alpha_market")) as mc:
            with mc.cursor() as cur:
                cur.execute("""
                    SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND ask_px > bid_px AND bid_px > 0
                    ORDER BY event_ts_ms
                """, (sym + "USDT", int(t0 * 1000), int((t0 + horizon) * 1000)))
                seq = [(int(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
        if len(seq) >= 5:
            out.append((sym, side, seq))
    return out


def simulate(seqs, *, tp: float, sl: float, r: float | None,
             hold_s: float, horizon: float) -> dict:
    """对每笔入场模拟出库，返回三条路径的占比与整体每笔净 bp。

    `r=None` ⇒ 该方案**不挂减仓侧出库单**（纯止盈/止损/超时）。
    """
    n = len(seqs)
    if n == 0:
        return {"n": 0, "overall": 0.0, "tp": 0, "sl": 0, "passive": 0, "timeout": 0, "open": 0}
    tot = 0.0
    c_tp = c_sl = c_pas = c_to = c_open = 0
    for (sym, side, seq) in seqs:
        b0, a0 = seq[0][1], seq[0][2]
        mid0 = (b0 + a0) / 2.0
        if mid0 <= 0:
            continue
        hs = (a0 - b0) / 2.0
        sgn = 1.0 if side == "buy" else -1.0          # +1 多头，-1 空头
        t_start = seq[0][0]
        exit_off = (r * hs) / mid0 * 1e4 if r else None
        done = False
        for (ms, b, aa) in seq:
            mid = (b + aa) / 2.0
            fav = sgn * (mid - mid0) / mid0 * 1e4      # 有利为正
            # 判定顺序：不利优先（风控先跑，与引擎一致：止损在止盈之前）
            if fav <= -sl:
                tot += -(sl + TAKER_BP); c_sl += 1; done = True; break
            if fav >= tp:
                tot += (tp - TAKER_BP); c_tp += 1; done = True; break
            if exit_off is not None and fav >= exit_off:
                tot += exit_off; c_pas += 1; done = True; break
            if (ms - t_start) / 1000.0 >= hold_s:
                # 超时：按当时浮盈结算，付 taker
                tot += fav - TAKER_BP; c_to += 1; done = True; break
        if not done:
            mid = (seq[-1][1] + seq[-1][2]) / 2.0
            fav = sgn * (mid - mid0) / mid0 * 1e4
            tot += fav; c_open += 1
    return {"n": n, "overall": tot / n, "tp": c_tp, "sl": c_sl,
            "passive": c_pas, "timeout": c_to, "open": c_open}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=float, default=300.0)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--tp", type=float, default=12.0)
    ap.add_argument("--sl", type=float, default=40.0)
    ap.add_argument("--hold", type=float, default=300.0)
    a = ap.parse_args()

    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    lim = j.get("limits") or {}
    print("=" * 100)
    print("H173  出库策略全规则模拟（三条路径竞争，先发生者胜）")
    print("=" * 100)
    print(f"  止盈 tp={a.tp}bp（净 +{a.tp-TAKER_BP}）   "
          f"止损 sl={a.sl}bp（净 −{a.sl+TAKER_BP}）   "
          f"超时 {a.hold:.0f}s   回放 {a.horizon:.0f}s")
    print(f"  当前生效：take_profit_bp={lim.get('take_profit_bp')}  "
          f"spread_mult_reduce={(j.get('params') or {}).get('spread_mult_reduce')}")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]
            cur.execute("""
                SELECT id, ts, symbol, meta_json->>'side'
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
                  AND coalesce(meta_json->>'flatten','false')='false'
                ORDER BY id
            """, (LANE, since))
            entries = cur.fetchall()[-a.limit:]
    print(f"  入场样本 {len(entries)} 笔，取盘口序列中 …")
    seqs = load_sequences(entries, a.horizon)
    print(f"  可用样本 {len(seqs)} 笔\n")

    plans = [
        ("A 现状：出库 r=0.4 + 止盈 12", dict(tp=a.tp, sl=a.sl, r=0.4, hold_s=a.hold, horizon=a.horizon)),
        ("B 纯止盈：不挂出库单", dict(tp=a.tp, sl=a.sl, r=None, hold_s=a.hold, horizon=a.horizon)),
        ("C 折中：出库 r=1.5 + 止盈 12", dict(tp=a.tp, sl=a.sl, r=1.5, hold_s=a.hold, horizon=a.horizon)),
        ("D 纯止损：不挂出库单、无止盈", dict(tp=1e9, sl=a.sl, r=None, hold_s=a.hold, horizon=a.horizon)),
        ("E 纯被动：出库 r=0.4、无止盈", dict(tp=1e9, sl=a.sl, r=0.4, hold_s=a.hold, horizon=a.horizon)),
    ]

    print(f"  {'方案':<32} {'每笔净bp':>10} {'止盈%':>7} {'止损%':>7} {'被动%':>7} "
          f"{'超时%':>7} {'未平%':>7}")
    print("  " + "-" * 84)
    res = []
    for name, kw in plans:
        r = simulate(seqs, **kw)
        n = max(r["n"], 1)
        res.append((name, r["overall"]))
        print(f"  {name:<32} {r['overall']:>+10.4f} {r['tp']/n*100:>6.1f}% "
              f"{r['sl']/n*100:>6.1f}% {r['passive']/n*100:>6.1f}% "
              f"{r['timeout']/n*100:>6.1f}% {r['open']/n*100:>6.1f}%")

    print(f"\n  现状实测基准（真实账本每笔净额）= **+0.1374 bp**")
    best = max(res, key=lambda x: x[1])
    print(f"\n  ⇒ 模拟最优：**{best[0]}**（{best[1]:+.4f} bp/笔）")
    print(f"\n  ── 读法 ──")
    print(f"    · 看「止盈%」是否 > 0：若为 0，说明该方案里止盈根本没机会触发")
    print(f"    · 「纯止盈」(B) 若优于现状 ⇒ **该撤掉减仓侧出库单**，")
    print(f"      让仓位活着等有利移动，+12bp 落袋（付 4bp）比 +0.2bp 白送（免费）好得多")
    print(f"    · 「纯被动」(E) 是「完全不靠止盈」的对照，用来确认止盈有没有正贡献")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
