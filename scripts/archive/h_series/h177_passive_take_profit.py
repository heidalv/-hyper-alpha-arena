# -*- coding: utf-8 -*-
"""[H177 2026-09-21] 止盈的**被动版**：挂在 +T 处等成交，而不是主动打对手价。

# 为什么值得先试这个（而不是直接执行方案 ①）

H176 实测三条路径的真实占比与代价：

    A 止盈（有利 ≥12bp）  109 笔  54.5%   +8.0bp   → 加权 +4.360
    B 止损（不利 ≥40bp）   15 笔   7.5%  −44.0bp   → 加权 −3.300
    D 都没发生             76 笔  38.0%   → 300s 时**中位 −15.89bp**（已经在亏）
    ⇒ A+B 合计 **+1.060 bp/笔**

**问题**：撤掉出库单后，D 类那 38% 不能再靠 +0.2bp 的挂单悄悄走掉，
会暴露成 −15.89bp 的浮亏。净期望虽为正，但**三种结局（+8 / −44 / −15.9）方差极大**。

# 被动版止盈：同样是"在有利价位出库"，但**不付 taker 费**

  · 多头：在 `mid0 × (1 + T/1e4)` 挂**卖单** ⇒ 价格涨到那里，被买方主动打到 ⇒ **maker 成交**
  · 空头：在 `mid0 × (1 − T/1e4)` 挂**买单**
  · 判定与引擎同语义：多头需 `bid ≥ 挂单价`，空头需 `ask ≤ 挂单价`
  · 成交后净 = **+T bp（免费）**，而不是 `+T − 4bp`

⇒ 期望更高（多 4bp/次）且**不需要付任何费**；代价是"必须真被吃到"（H165 已证
严格口径触发率 32.0% ≈ 乐观 31.3%，说明这类池子里价格到过就能成交）。

# 本脚本对比

  P（现有）：出库 r=0.4 的窄挂单（赚 ~0.2bp，几乎必成交）
  Q（被动止盈）：出库单挂到 **+T bp**（赚 +T bp，需价格真到）
  R（主动止盈）：到 +T 就打对手价（赚 +T−4bp，H176 已量）

三条路径都叠加 ① 止损 40bp ② 300s 超时后按当时浮盈 taker 结算。

# 判据

  · 取整体每笔净 bp 最高者
  · 同时看**方差**（标准差）—— 微利策略应优先选波动小的
  · 若 Q 的期望 ≥ R 且方差更低 ⇒ 用 Q（被动版止盈），**不必执行方案 ①**

用法：
    .venv\\Scripts\\python.exe scripts\\h177_passive_take_profit.py
"""
from __future__ import annotations

import argparse
import statistics as st
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--horizon", type=float, default=300.0)
    ap.add_argument("--sl", type=float, default=40.0)
    ap.add_argument("--hold", type=float, default=300.0)
    ap.add_argument("--tp-ladder", default="8,12,20")
    a = ap.parse_args()

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

    print("=" * 100)
    print("H177  止盈的被动版 vs 主动版（含 D 类的真实代价）")
    print("=" * 100)
    print(f"  止损 {a.sl}bp   超时 {a.hold:.0f}s（超时按当时浮盈 taker 结算）   "
          f"窗口 {a.horizon:.0f}s")

    seqs = []
    for i, (rid, ts, sym, side) in enumerate(entries, 1):
        t0 = ts.timestamp()
        with psycopg.connect(dsn("alpha_market")) as mc:
            with mc.cursor() as cur:
                cur.execute("""
                    SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                      AND ask_px > bid_px AND bid_px > 0
                    ORDER BY event_ts_ms
                """, (sym + "USDT", int(t0 * 1000), int((t0 + a.horizon) * 1000)))
                sq = [(int(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]
        if len(sq) >= 5:
            seqs.append((sym, side, sq))
        if i % 60 == 0:
            print(f"    取盘口 {i}/{len(entries)} …")
    print(f"  可用样本 {len(seqs)} 笔\n")

    def run(mode: str, tp: float) -> dict:
        """mode: 'passive_quote' | 'active_taker' | 'narrow_quote'"""
        res = []
        paths = {"tp": 0, "sl": 0, "quote": 0, "timeout": 0, "open": 0}
        for (sym, side, sq) in seqs:
            mid0 = (sq[0][1] + sq[0][2]) / 2.0
            if mid0 <= 0:
                continue
            hs = (sq[0][2] - sq[0][1]) / 2.0
            sgn = 1.0 if side == "buy" else -1.0
            t0 = sq[0][0]
            # 出库单价（bp 偏移）
            if mode == "narrow_quote":
                q_off = 0.4 * hs / mid0 * 1e4
            elif mode == "passive_quote":
                q_off = tp
            else:
                q_off = None
            done = False
            for (ms, b, aa) in sq:
                mid = (b + aa) / 2.0
                fav = sgn * (mid - mid0) / mid0 * 1e4
                # 先风控
                if fav <= -a.sl:
                    res.append(-(a.sl + TAKER_BP)); paths["sl"] += 1; done = True; break
                # 主动止盈
                if mode == "active_taker" and fav >= tp:
                    res.append(tp - TAKER_BP); paths["tp"] += 1; done = True; break
                # 挂单出库（被动）：与引擎同语义
                if q_off is not None:
                    if sgn > 0:
                        hit = b >= mid0 * (1 + q_off / 1e4)      # 多头卖出：bid 打到我们的卖价
                    else:
                        hit = aa <= mid0 * (1 - q_off / 1e4)     # 空头买入：ask 打到我们的买价
                    if hit:
                        gain = q_off if mode == "narrow_quote" else tp
                        if mode == "passive_quote" and fav >= tp:
                            gain = tp          # 被动止盈成交，免费
                        res.append(gain); paths["quote"] += 1; done = True; break
                if (ms - t0) / 1000.0 >= a.hold:
                    res.append(fav - TAKER_BP); paths["timeout"] += 1; done = True; break
            if not done:
                mid = (sq[-1][1] + sq[-1][2]) / 2.0
                res.append(sgn * (mid - mid0) / mid0 * 1e4); paths["open"] += 1
        n = max(len(res), 1)
        return {"n": len(res), "mean": st.mean(res), "sd": st.pstdev(res) if len(res) > 1 else 0.0,
                "median": st.median(res), "paths": paths}

    print(f"  {'方案':<34} {'每笔净bp':>10} {'标准差':>9} {'中位':>9} 路径构成")
    print("  " + "-" * 92)
    plans = [("现况：窄挂单 r=0.4（无止盈）", "narrow_quote", 0.0)]
    for tp in [float(x) for x in a.tp_ladder.split(",")]:
        plans.append((f"被动止盈 +{tp:.0f}bp（挂单等成交，免费）", "passive_quote", tp))
    for tp in [float(x) for x in a.tp_ladder.split(",")]:
        plans.append((f"主动止盈 +{tp:.0f}bp（打对手价，付4bp）", "active_taker", tp))

    rows = []
    for name, mode, tp in plans:
        r = run(mode, tp)
        rows.append((name, r))
        p = r["paths"]
        comp = (f"挂单{p['quote']} 超时{p['timeout']} 止损{p['sl']} "
                f"止盈{p['tp']} 未平{p['open']}")
        print(f"  {name:<34} {r['mean']:>+10.4f} {r['sd']:>9.3f} {r['median']:>+9.3f} {comp}")

    print(f"\n  ── 判读 ──")
    print(f"    · 对比「标准差」：微利策略应优先选**波动小**的方案，")
    print(f"      因为大方差会让净值路径剧烈摇摆（用户的「亏没了」感受就来自这里）")
    best = min(rows, key=lambda x: -x[1]["mean"] + x[1]["sd"] * 0.5)
    print(f"\n  ⇒ 按「均值 − 0.5×标准差」排序最优：**{best[0]}**")
    print(f"     （每笔 {best[1]['mean']:+.4f}bp，标准差 {best[1]['sd']:.3f}）")
    print(f"\n  ⚠️ 口径说明：本模拟的「现况」行应与实盘基准 +0.1374bp 接近才算可信；")
    print(f"     若差距大，说明模拟仍未对齐实盘，**只能用于方案间的相对比较**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
