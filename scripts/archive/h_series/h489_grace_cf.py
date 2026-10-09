"""h489：宽限期反事实——"等不到被动成交时，应该多久后改 taker？"

背景（h472 之后的新常态）：
  `ofi_flatten` 改成 maker-only 后，等不到对手方的仓位会一路走到
  `timeout_hard_taker_sec = 300s` 的**合规硬顶**才成交（费率 −4bp）。
  实测代价：硬顶率由基线 2.9/h 升到 ~8/h。若"提前认输、主动 taker"更划算，
  正确设计是**被动优先 + 宽限 N 秒后仍不成交则 taker**，而不是等到 300s。

本脚本用**真实中价路径**（`asterdex_book_ticker`）对每个"硬顶出场"的往返做反事实：

  对 T ∈ {60, 120, 180, 240, 300} 秒（自**开仓时刻**起）：
      假设在 T 秒时以 taker 出场 ⇒ 相对开仓均价的价差项
        = (mid_T − avg_entry)/avg_entry × 1e4  − 半价差  （多头；空头取反）
      净额 ≈ 上式 − taker_fee_bp
  与**实际硬顶成交**的已实现 net_bp 对比 ⇒ 给出"提前认输"的收益/损失曲线。
  另外给出对照组：**被动成交（ofi_flatten_maker）**腿的实际结果。

口径注意（h487/h488 的教训）：
  · 账本 `ts` 是**检测时刻**，滞后真实穿越 ≈45–60s（延迟判定）⇒
    本脚本的 T 以**开仓腿的 ts** 为锚，两端同样滞后 ⇒ 差值基本无偏；
  · 中价用 `(bid+ask)/2`，取自 5s 桶，命中失败则该 T 记缺失（不插值）。

用法：python scripts/h489_grace_cf.py [--since 2026-09-28T18:33:15+00:00]
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h489_grace_cf.json"
TS = (60, 120, 180, 240, 300)
TAKER_FEE_BP = 4.0
HALF_SPREAD_BP = 0.9     # 现场 `avg_width_bp ≈ 0.87–0.96`（h486）


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def mid_at(cur2, sym: str, t_sec: float):
    """取 t_sec 附近（±6s）最接近的中价；无数据返回 None。"""
    t0 = int(t_sec * 1000)
    cur2.execute(
        "SELECT (event_ts_ms/5000)*5 AS b, "
        "(ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1] "
        "FROM asterdex_book_ticker WHERE symbol=%s "
        "AND event_ts_ms > %s AND event_ts_ms <= %s "
        "AND bid_px>0 AND ask_px>bid_px GROUP BY 1 ORDER BY 1",
        (sym + "USDT", t0 - 6000, t0 + 6000))
    g = {int(b): float(m) for b, m in cur2.fetchall()}
    if not g:
        return None
    k = min(g, key=lambda x: abs(x - t_sec))
    return g[k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-28T18:33:15+00:00",
                    help="统计起点（默认 = h472 部署时刻）")
    a = ap.parse_args()
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, position_id, COALESCE(meta_json->>'exit_path',''), "
                " (meta_json->>'qty')::float8, (meta_json->>'fill_px')::float8, "
                " net_bp::float8, notional::float8 "
                "FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz ORDER BY ts",
                (LANE, a.since))
            rows = cur.fetchall()
    # 顺序状态机配平往返（position_id 会被复用，不能按 id 去重）
    seq: dict = {}
    trips = []
    for ts, sym, pid, ep, qty, px, net, noti in rows:
        d = seq.setdefault((pid, sym), {"open_ts": None, "qty": 0.0, "cost": 0.0})
        if ep == "":
            if d["open_ts"] is None:
                d["open_ts"], d["qty"], d["cost"] = ts, 0.0, 0.0
            d["qty"] += abs(float(qty or 0.0))
            d["cost"] += abs(float(qty or 0.0)) * float(px or 0.0)
        else:
            if d["open_ts"] is not None:
                avg = d["cost"] / d["qty"] if d["qty"] else 0.0
                trips.append({"sym": sym, "path": ep, "open_ts": d["open_ts"],
                              "exit_ts": ts, "avg_entry": avg, "qty": d["qty"],
                              "net_bp": float(net or 0.0),
                              "notional": float(noti or 0.0),
                              "hold": (ts - d["open_ts"]).total_seconds()})
                d["open_ts"] = None
    caps = [t for t in trips if t["path"] == "timeout_hard_taker"]
    passives = [t for t in trips if t["path"] == "ofi_flatten_maker"]
    print(f"自 {a.since}：往返 {len(trips)} 次 | 硬顶出场 {len(caps)} | "
          f"被动成交(ofi_flatten_maker) {len(passives)}")
    if not caps:
        print("暂无硬顶出场样本（稍后再跑）")
        return 0
    with psycopg.connect(mk, autocommit=True) as c2:
        with c2.cursor() as cur2:
            for t in caps:
                t0 = t["open_ts"].timestamp()
                path = {}
                for T in TS:
                    m = mid_at(cur2, t["sym"], t0 + T)
                    if m is None or t["avg_entry"] <= 0:
                        path[T] = None
                        continue
                    # 多头/空头方向：用 qty 的符号（配平时用净额方向近似）
                    sgn = 1.0 if t["net_bp"] != 0 else 0.0
                    raw = (m - t["avg_entry"]) / t["avg_entry"] * 1e4
                    path[T] = raw - HALF_SPREAD_BP - TAKER_FEE_BP
                t["alt"] = path
    print("=" * 96)
    print(f"{'币':6s} {'持仓s':>6s} {'实际净bp':>9s} " +
          " ".join(f"{('T+'+str(T)):>9s}" for T in TS))
    for t in caps:
        row = " ".join(f"{(t['alt'][T] if t['alt'][T] is not None else float('nan')):9.1f}"
                       for T in TS)
        print(f"{t['sym']:6s} {t['hold']:6.0f} {t['net_bp']:9.1f} {row}")
    print("=" * 96)
    real = [t["net_bp"] for t in caps]
    print(f"实际（等到硬顶）: n={len(real)} 均={st.mean(real):+.2f}bp")
    summary = {"n_caps": len(caps), "real_mean_bp": round(st.mean(real), 3)}
    for T in TS:
        xs = [t["alt"][T] for t in caps if t["alt"][T] is not None]
        if len(xs) < 3:
            print(f"  提前到 T+{T:3d}s 认输: 样本不足 ({len(xs)})")
            continue
        m = st.mean(xs)
        sd = st.stdev(xs) if len(xs) > 1 else 0.0
        tt = m / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0
        d = m - st.mean(real)
        print(f"  提前到 T+{T:3d}s 认输: n={len(xs)} 均={m:+.2f}bp "
              f"(t={tt:+.1f})  相对硬顶 {d:+.2f}bp")
        summary[f"T{T}"] = {"n": len(xs), "mean_bp": round(m, 3),
                            "t": round(tt, 2), "delta_vs_cap": round(d, 3)}
    if passives:
        pn = [t["net_bp"] for t in passives]
        print(f"\n对照组（被动成交 ofi_flatten_maker）: n={len(pn)} "
              f"均={st.mean(pn):+.2f}bp 中位={st.median(pn):+.2f}bp")
        summary["passive"] = {"n": len(pn), "mean_bp": round(st.mean(pn), 3)}
    best = None
    for T in TS:
        k = f"T{T}"
        if k in summary and summary[k]["n"] >= 3:
            if best is None or summary[k]["mean_bp"] > summary[f"T{best}"]["mean_bp"]:
                best = T
    if best is not None:
        d = summary[f"T{best}"]["delta_vs_cap"]
        if d > 0.5:
            verdict = (f"**宽限 {best}s 更优**：反事实比等到硬顶多 {d:+.2f}bp/笔 ⇒ "
                       f"应实现「被动优先 + {best}s 后仍不成交则 taker」")
        elif d < -0.5:
            verdict = (f"等到硬顶反而更好（最佳宽限 {best}s 仍差 {d:+.2f}bp）⇒ "
                       f"维持现状，不要提前认输")
        else:
            verdict = f"各宽限与硬顶差异不显著（最佳 {best}s，Δ={d:+.2f}bp）⇒ 样本不足或本就中性"
    else:
        verdict = "样本不足，无法裁决（需更多硬顶样本，或把 T 网格细化）"
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps({"since": a.since, "trips": len(trips), "caps": caps,
                               "summary": summary, "verdict": verdict},
                              ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
