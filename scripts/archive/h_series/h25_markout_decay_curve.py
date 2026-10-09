"""H25：markout 随持有时间怎么衰减 —— 定位"该持多久"的拐点。

## 为什么这是现在最关键的一张图

H24 反事实（8h，4 币，队列消耗制，markout **从限价**算）：
    A 现状 mid±1.5bp  成交率 26.2%  半价差 1.50bp
        净@1s +1.272bp   净@5s +1.429bp   净@30s **+1.718bp**
    B 贴 touch        成交率 26.8%  半价差 0.59bp   净@30s +0.756bp
**两条都为正**（A 更优）。而实盘是 **−0.60bp/笔**。

差异不在报价位置（H24 已否掉那条假设），而在**时间尺度**：
  · 反事实的净额在 1–30 秒上是正的
  · 实盘的 `price_bp = −2.02bp` 是整个**持仓期**（中位几十秒，上限 900s）

⇒ 存在一个**最优持有时间**，超过它 markout 的衰减吃掉价差捕获。
本脚本把 markout 曲线画到 30 分钟，直接读出拐点。

## 会计口径（Thogiti 恒等式）

    Π(τ) = s/2 − |ΔM(τ)|
    · `s/2` = 半价差（挂多宽，成交瞬间就锁定多少）
    · `ΔM(τ)` = 从**限价**到成交后 τ 时刻中价的漂移
    ⇒ 净额在 τ 上先平后负，拐点即**理论最优持有时间**

## 判据（事先定死）

  · 若 Π(τ) 在某个 τ* 处穿过 0 ⇒ τ* 就是持有时间上限，**这是可直接用作
    `max_one_side_seconds` 的值**（替代拍脑袋的 900s）。
  · 若 Π(τ) 在所有 τ ≤ 30min 上都为正 ⇒ 持仓时长不是问题，另找原因。
  · 若 Π(τ) 一上来就为负 ⇒ 半价差根本不够，问题在挂宽（与 H24 冲突，需复查）。

用法：
    .venv\\Scripts\\python.exe scripts\\h25_markout_decay_curve.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "research_l1" / "out"
# Thogiti 建议的 logger 网格（0.1/0.5/1/2/5/10s）+ 长端到 30 分钟
TAUS_MS = [100, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000,
           600000, 1800000]


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--symbols", default="ASTERUSDT,XRPUSDT,SOLUSDT,DOGEUSDT")
    ap.add_argument("--quote-every-s", type=float, default=60.0)
    ap.add_argument("--max-wait-s", type=float, default=300.0)
    ap.add_argument("--width-bp", type=float, default=1.5,
                    help="报价半宽（bp）；用线上实际值")
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(args.hours * 3600_000)}"

    print("H25 markout 衰减曲线（markout 从限价算；Π = s/2 − |ΔM|）")
    print(f"窗口={args.hours}h  币={len(syms)}  报价节奏={args.quote_every_s:.0f}s  "
          f"半宽={args.width_bp}bp\n")

    curves = []
    for s in syms:
        cur.execute(
            "SELECT event_ts_ms, bids, asks FROM asterdex_depth_snapshots"
            f" WHERE symbol = %s AND event_ts_ms > {since} ORDER BY event_ts_ms",
            (s,),
        )
        d = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms, price::float AS p, qty::float AS q, is_buyer_maker AS ibm"
            f"  FROM asterdex_trades WHERE symbol = %s AND event_ts_ms > {since}"
            " ORDER BY event_ts_ms",
            (s,),
        )
        tr = cur.fetchall()
        if len(d) < 300 or len(tr) < 200:
            print(f"  {s:<12} 数据不足 → 跳过")
            continue
        dts = np.array([int(x["event_ts_ms"]) for x in d], dtype=np.int64)
        tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
        tpx = np.array([float(x["p"]) for x in tr])
        tq = np.array([float(x["q"]) for x in tr])
        tsell = np.array([bool(x["ibm"]) for x in tr])
        mids = np.array([
            (float(x["bids"][0][0]) + float(x["asks"][0][0])) / 2.0
            if x["bids"] and x["asks"] else np.nan for x in d])

        def mid_at(t_ms, delta_ms):
            i = int(np.searchsorted(dts, t_ms + delta_ms, "left"))
            if i >= len(dts):
                return None
            m = mids[i]
            return float(m) if np.isfinite(m) else None

        step = max(1, int(args.quote_every_s * 1000 /
                          max(1, int(np.median(np.diff(dts)) or 2000))))
        fills = []
        for i in range(0, len(d), step):
            x = d[i]
            if not x["bids"] or not x["asks"]:
                continue
            bb = float(x["bids"][0][0])
            bq = float(x["bids"][0][1])
            ba = float(x["asks"][0][0])
            mid = (bb + ba) / 2.0
            px = mid * (1.0 - args.width_bp / 1e4)
            if px <= 0:
                continue
            t0 = int(dts[i])
            j = int(np.searchsorted(tts, t0, "left"))
            lim = int(np.searchsorted(tts, t0 + int(args.max_wait_s * 1000), "left"))
            cum = 0.0
            fill_j = -1
            while j < lim:
                if tsell[j] and abs(tpx[j] - px) / px * 1e4 < 1.0:
                    cum += float(tq[j])
                    if cum >= bq:
                        fill_j = j
                        break
                j += 1
            if fill_j < 0:
                continue
            tf = int(tts[fill_j])
            rec = {"hs_bp": (mid - px) / mid * 1e4}
            for tau in TAUS_MS:
                m = mid_at(tf, tau)
                rec[tau] = ((m / px - 1.0) * 1e4) if m else None
            fills.append(rec)
        if not fills:
            continue
        curves.append({"symbol": s, "n": len(fills), "fills": fills})
        print(f"  {s:<12} 成交 {len(fills):>5} 笔")

    if not curves:
        print("\n无成交样本")
        return 1

    allf = [r for c in curves for r in c["fills"]]
    hs = np.array([r["hs_bp"] for r in allf])
    print(f"\n合计成交 {len(allf)} 笔   半价差均值 = {hs.mean():.4f} bp\n")

    print("[曲线]  τ        ΔM(τ)均值   |ΔM|      Π = s/2 − |ΔM|   n")
    rows = []
    for tau in TAUS_MS:
        v = np.array([r[tau] for r in allf if r.get(tau) is not None])
        if len(v) < 30:
            continue
        dm = float(v.mean())
        pi = float(hs.mean() - abs(dm))
        rows.append({"tau_ms": tau, "dm_bp": dm, "abs_dm_bp": abs(dm),
                     "pi_bp": pi, "n": int(len(v))})
        lab = (f"{tau/1000:.1f}s" if tau < 60000 else f"{tau/60000:.0f}min")
        print("    %-8s %+10.3f %10.3f %14.3f %6d" % (lab, dm, abs(dm), pi, len(v)))

    print("\n[判定] 最优持有时间")
    pos = [r for r in rows if r["pi_bp"] > 0]
    if not pos:
        print("    ⇒ **所有 τ 的 Π 都为负** ⇒ 半价差不够覆盖任何时点的 markout。")
        print("      与 H24 冲突（H24 的净@1s 为正）⇒ 需复查两处 markout 的口径差异。")
    else:
        last_pos = max(pos, key=lambda r: r["tau_ms"])
        nxt = [r for r in rows if r["tau_ms"] > last_pos["tau_ms"]]
        lab = (f"{last_pos['tau_ms']/1000:.1f}s" if last_pos["tau_ms"] < 60000
               else f"{last_pos['tau_ms']/60000:.0f}min")
        print("    Π 仍为正的最大 τ = **%s**（Π = %+.3fbp）" % (lab, last_pos["pi_bp"]))
        if nxt:
            n0 = nxt[0]
            lab0 = (f"{n0['tau_ms']/1000:.1f}s" if n0["tau_ms"] < 60000
                    else f"{n0['tau_ms']/60000:.0f}min")
            print("    穿过 0 的区间: (%s, %s]  ⇒ **理论持有上限 ≈ %s**"
                  % (lab, lab0, lab0))
            print("    ⇒ 建议把 `max_one_side_seconds` 设为该值的 1~2 倍（留噪声余量），")
            print("      而不是现在的 %s。**这是数据给出的值，不是拍脑袋。**" % "900s")
        else:
            print("    在 τ ≤ 30min 的整个范围上 Π 都为正 ⇒ 持仓时长不是（30 分钟内的）问题。")
            print("    ⇒ 实盘 −0.60bp 的原因在别处：检查 ① 挂单是否真在 touch 附近")
            print("      ② 成交判定是否偏毒 ③ 是否有非 markout 的成本项（强平等）。")

    print("\n  口径提醒：markout **从限价**算，买单用 (mid/px − 1)，与论文 Table 1 一致；")
    print("  只统计**买侧**（卖侧对称，未跑）；未成交样本截断在 %.0fs。" % args.max_wait_s)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h25_markout_decay_curve.json"
    p.write_text(json.dumps({"hours": args.hours, "symbols": syms,
                             "width_bp": args.width_bp,
                             "half_spread_bp": float(hs.mean()),
                             "n_fills": len(allf), "curve": rows},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
