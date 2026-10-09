# -*- coding: utf-8 -*-
"""H446 出场时机反事实：强平后 +60s/+300s 的价格走向 ⇒ 出场是"正确截断"还是"过早"。

口径：对每条强平腿（stop/jump/timeout/trail/reversal/ofi_flatten），取
   d_post(h) = 出场方向的有利漂移 = −(mid_{t+h} − mid_t)/mid_t × 1e4  (卖/平多)
                                  = +(mid_{t+h} − mid_t)/mid_t × 1e4  (买/平空)
   d_post > 0 ⇒ 出场后价格继续朝我们离场方向走 ⇒ 出场正确（避免了更大损失）
   d_post < 0 ⇒ 出场后价格回来 ⇒ 出场过早（本可少亏/盈利）
用法: python scripts/h446_exit_counterfactual.py [--hours 48]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h446_exit_counterfactual.json"


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


def _stats(xs):
    n = len(xs)
    if n < 5:
        return {"n": n, "mean": 0.0, "t": 0.0}
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return {"n": n, "mean": round(m, 3),
            "t": round(m / math.sqrt(var / n), 2) if var > 0 else 0.0}


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()

    import psycopg
    print(f"窗口 {a.hours}h：加载强平腿……", flush=True)
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT ts, symbol, meta_json->>'side', meta_json->>'exit_path',
                       net_bp, (meta_json->>'qty')::float8
                FROM lane_ledger
                WHERE event='fill' AND ts >= now() - interval '%s hours'
                  AND meta_json->>'exit_path' LIKE '%%taker%%'
                ORDER BY ts
            """ % a.hours)
            legs = cur.fetchall()
    print(f"强平腿 {len(legs)} 条", flush=True)

    # 批量取 mid：按币缓存 5s 网格
    out = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for ts, sym, side, path, net, qty in legs:
                t0 = int(ts.timestamp())
                # 取 [t, t+300s] 的价格路径（5s 桶末）
                cur.execute("""
                    SELECT (event_ts_ms/5000)*5 AS b,
                           (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms > %s AND event_ts_ms <= %s
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY 1 ORDER BY 1
                """, (sym + "USDT", t0 * 1000, (t0 + 305) * 1000))
                grid = {int(b): float(m) for b, m in cur.fetchall()}
                if not grid:
                    continue
                ks = sorted(grid)
                base = grid[ks[0]]
                if base <= 0:
                    continue
                sign = -1.0 if side == "sell" else 1.0   # 离场方向的有利漂移
                for h_sec, hname in ((60, "p60"), (300, "p300")):
                    k = min(ks, key=lambda x: abs(x - (t0 + h_sec)))
                    d = sign * (grid[k] - base) / base * 1e4
                    out.setdefault(path, {}).setdefault(hname, []).append(d)
    res = {}
    print(f"\n{'出场路径':<26}{'n':>5}{'+60s 漂移':>12}{'t':>7}{'+300s 漂移':>13}{'t':>7}  判定")
    for path in sorted(out):
        s60 = _stats(out[path].get("p60", []))
        s300 = _stats(out[path].get("p300", []))
        verd = ("出场正确（避免更大损失）" if s60["mean"] > 1 and s60["t"] > 1.5 else
                "出场过早（价格回来了）" if s60["mean"] < -1 and s60["t"] < -1.5 else
                "中性")
        print(f"{path:<26}{s60['n']:>5}{s60['mean']:>+12.2f}{s60['t']:>+7.1f}"
              f"{s300['mean']:>+13.2f}{s300['t']:>+7.1f}  {verd}")
        res[path] = {"p60": s60, "p300": s300, "verdict": verd}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
