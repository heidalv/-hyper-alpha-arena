# -*- coding: utf-8 -*-
"""H460 闭环验证：模型预测 d×Δmid(60s) vs 实际成交腿 net_bp —— 实现效率（capture ratio）。

方法：取近 24h 带 `quote_ts` 的成交腿（h413 起记录挂单时刻，无桶延迟污染）：
  · d = 该腿方向（buy=+1 / sell=−1）
  · 预测 = d × Δmid(60s)（用 5s 网格，从挂单时刻起 60s）
  · 实际 = 该腿 net_bp（含费/滑点的落袋）
  ⇒ IC = corr(预测, 实际)；斜率 = 实际/预测（capture ratio）；
     并按状态桶（OFI×形态方向、|r300|）分组看预测与实际。
用法: python scripts/h460_closed_loop.py [--hours 24]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "research_l1" / "out" / "h456_grid5_cache.json"
OUT = ROOT / "research_l1" / "out" / "h460_closed_loop.json"


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


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()

    import psycopg
    c = psycopg.connect(read_env_dsn(), autocommit=True)
    cur = c.cursor()
    cur.execute("""
        SELECT ts, symbol, meta_json->>'side' AS side, net_bp,
               (meta_json->>'quote_ts')::float8 AS qts
        FROM lane_ledger
        WHERE event='fill' AND ts >= now() - interval '%s hours'
          AND (meta_json->>'quote_ts') ~ '^[0-9.]+$'
          AND (meta_json->>'quote_ts')::float8 > 0
        ORDER BY ts
    """ % a.hours)
    legs = cur.fetchall()
    print(f"近 {a.hours}h 带 quote_ts 腿：{len(legs)}", flush=True)

    j = json.loads(CACHE.read_text(encoding="utf-8"))
    grids = {s: {int(k): float(v) for k, v in g.items()} for s, g in j["grids"].items()}
    ofi = {s: {int(k): float(v) for k, v in d.items()} for s, d in (j.get("ofi") or {}).items()}

    rows = []
    for ts, sym, side, net, qts in legs:
        g = grids.get(sym)
        if not g or not qts:
            continue
        t = int(qts)
        base = int(t) // 5 * 5
        keys = list(g.keys())
        i = bisect.bisect_left(keys, base)
        if i <= 0 or i + 12 >= len(keys):
            continue
        tk = keys[i]
        m0 = g[tk]
        m60 = g.get(tk + 60) or g.get(keys[min(i + 12, len(keys) - 1)])
        if not m0 or not m60 or m0 <= 0:
            continue
        d = 1.0 if str(side) == "buy" else -1.0
        pred = d * (m60 - m0) / m0 * 1e4
        # 状态：形态方向 = 逆 r60；fo = OFI × 形态方向
        m_before = g.get(tk - 60)
        r60 = ((m0 - m_before) / m_before * 1e4) if (m_before and m_before > 0) else 0.0
        fade_d = -1.0 if r60 > 0 else (1.0 if r60 < 0 else 0.0)
        f = ofi.get(sym, {}).get((tk // 15) * 15, 0.0)
        fo = f * fade_d
        aligned = (d == fade_d)
        rows.append((sym, d, pred, float(net or 0.0), fo, aligned, abs(r60)))
    print(f"可归因腿 {len(rows)}", flush=True)
    if len(rows) < 100:
        print("样本不足")
        return 1

    preds = [r[2] for r in rows]
    reals = [r[3] for r in rows]
    n = len(preds)
    mp, mr = sum(preds) / n, sum(reals) / n
    cov = sum((preds[i] - mp) * (reals[i] - mr) for i in range(n)) / n
    sp = math.sqrt(sum((p - mp) ** 2 for p in preds) / n)
    sr = math.sqrt(sum((r - mr) ** 2 for r in reals) / n)
    ic = cov / (sp * sr) if sp > 0 and sr > 0 else 0.0
    slope = cov / (sp * sp) if sp > 0 else 0.0
    print(f"\n== 闭环（预测 = d×Δmid(60s)，实际 = 腿 net_bp）==")
    print(f"  预测均值 {mp:+.3f}bp | 实际均值 {mr:+.3f}bp | IC={ic:+.3f} | 斜率={slope:+.3f}")
    print(f"  ⇒ 实现捕获率 = 实际/预测 = {mr/mp if mp else 0:+.2%}" if mp else "")

    print(f"\n== 按状态桶（fo = OFI×形态方向）==")
    for lo, hi, lab in ((0.5, 9, "confirm≥0.5"), (0.0, 0.5, "弱确认"), (-9, 0.0, "逆流")):
        sel = [r for r in rows if lo <= r[4] < hi]
        if len(sel) < 20:
            continue
        p = sum(r[2] for r in sel) / len(sel)
        q = sum(r[3] for r in sel) / len(sel)
        al = sum(1 for r in sel if r[5]) / len(sel)
        print(f"  {lab:<12} n={len(sel):>4}  预测={p:+6.2f}bp  实际={q:+6.2f}bp  "
              f"捕获率={q/p if p else 0:+6.0%}  方向对齐腿占比={al:.0%}")

    print(f"\n== 按方向对齐 ==")
    for al, lab in ((True, "与形态方向一致"), (False, "与形态方向相反")):
        sel = [r for r in rows if r[5] == al]
        if len(sel) < 20:
            continue
        p = sum(r[2] for r in sel) / len(sel)
        q = sum(r[3] for r in sel) / len(sel)
        print(f"  {lab:<16} n={len(sel):>4} 预测={p:+6.2f}bp 实际={q:+6.2f}bp")

    OUT.write_text(json.dumps({"n": n, "pred_mean": round(mp, 4), "real_mean": round(mr, 4),
                               "ic": round(ic, 4), "slope": round(slope, 4)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
