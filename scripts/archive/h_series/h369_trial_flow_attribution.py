# -*- coding: utf-8 -*-
"""H369 P2 试跑时代腿 × OFI 流向已实现归因（h358 的实盘验证）。

h358 事件研究预测：P2 回归侧成交在"OFI 推离 VWAP"（against）时亏、OFI 已回归
（with）时赚。本脚本用**实盘试跑时代的真实成交**验证：
  - 取 h354 时代（stats_since 起）每条腿：side、成交时刻 ts、币种；
  - 成交时刻所在 15s OFI 桶（market_trades_aggregated，裸标的）；
  - flow = ofi × 方向（buy=+1/sell=−1）：with ≥ +0.3 / against ≤ −0.3 / neutral；
  - 分组已实现 net_bp（账本口径，与判定脚本同一数据源）。
对比基线（时代前 12h 同口径）。

用法: python scripts/h369_trial_flow_attribution.py
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h369_trial_flow_attribution.json"


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


def _stat(xs):
    n = len(xs)
    if n < 2:
        return {"n": n, "sum": round(sum(xs), 2), "mean": None}
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    t = m / math.sqrt(var / n) if var > 0 else 0.0
    return {"n": n, "sum": round(sum(xs), 2), "mean": round(m, 3), "t": round(t, 2)}


def main() -> int:
    import psycopg

    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'h354_p2_trial'->>'started_at' "
                        "FROM lane_registry WHERE lane_id='mm_asterdex'")
            since = cur.fetchone()[0]
            if not since:
                print("✗ 无 h354_p2_trial.started_at")
                return 1
            print("trial since:", since)
            cur.execute("""
                SELECT symbol, ts, meta_json->>'side' AS side, net_bp
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND ts > %s::timestamptz
            """, (since,))
            trial_rows = cur.fetchall()
            base_cut = cur.execute(
                "SELECT %s::timestamptz - interval '12 hours'", (since,)).fetchone()[0]
            cur.execute("""
                SELECT symbol, ts, meta_json->>'side' AS side, net_bp
                FROM lane_ledger
                WHERE lane_id='mm_asterdex'
                  AND ts > %s::timestamptz AND ts <= %s::timestamptz
            """, (base_cut, since))
            base_rows = cur.fetchall()

    # OFI 桶加载（每币，15s）
    import psycopg as _pg

    syms = sorted({r[0] for r in trial_rows + base_rows if r[0]})
    ofi_maps = {}
    with _pg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
        with c.cursor() as cur:
            for sym in syms:
                cur.execute("""
                    SELECT timestamp, COALESCE(taker_buy_notional,0), COALESCE(taker_sell_notional,0)
                    FROM market_trades_aggregated
                    WHERE symbol=%s AND timestamp >= (extract(epoch from now())*1000 - 40*3600*1000)::bigint
                    ORDER BY timestamp
                """, (sym,))
                m = {}
                for ts_ms, bn, sn in cur.fetchall():
                    tot = float(bn) + float(sn)
                    if tot > 0:
                        m[int(ts_ms) // 15000] = (float(bn) - float(sn)) / tot
                ofi_maps[sym] = m

    def classify(r):
        sym, ts, side, net_bp = r
        if side not in ("buy", "sell"):
            return None
        sign = 1.0 if side == "buy" else -1.0
        bucket = int(ts.timestamp()) // 15
        o = ofi_maps.get(sym, {}).get(bucket)
        if o is None:
            return None
        flow = ("with" if o * sign >= 0.3
                else ("against" if o * sign <= -0.3 else "neutral"))
        return flow, float(net_bp or 0.0)

    def group(rows):
        g = {"with": [], "neutral": [], "against": []}
        for r in rows:
            cl = classify(r)
            if cl:
                g[cl[0]].append(cl[1])
        return g

    t_g = group(trial_rows)
    b_g = group(base_rows)

    out = {}
    print(f"\n── 试跑时代（n={len(trial_rows)} 腿，可归类 {sum(len(v) for v in t_g.values())}）──")
    print(f"{'流向':<10} {'n':>6} {'∑bp':>9} {'均值bp':>9} {'t':>7}")
    for flow in ("with", "neutral", "against"):
        st = _stat(t_g[flow])
        out[f"trial_{flow}"] = st
        print(f"{flow:<10} {st['n']:>6} {st['sum']:>+9.1f} "
              f"{st['mean'] if st['mean'] is not None else '—':>9} "
              f"{st['t'] if st['t'] is not None else '—':>7}")

    print(f"\n── 基线 12h（n={len(base_rows)} 腿）──")
    for flow in ("with", "neutral", "against"):
        st = _stat(b_g[flow])
        out[f"baseline_{flow}"] = st
        print(f"{flow:<10} {st['n']:>6} {st['sum']:>+9.1f} "
              f"{st['mean'] if st['mean'] is not None else '—':>9} "
              f"{st['t'] if st['t'] is not None else '—':>7}")

    # 分币种 × 流向（试跑）
    print("\n── 试跑时代分币种（with / neutral / against 均值bp）──")
    per_sym = {}
    for sym in sorted({r[0] for r in trial_rows if r[0]}):
        sub = [r for r in trial_rows if r[0] == sym]
        g = group(sub)
        row = {flow: _stat(g[flow])["mean"] for flow in ("with", "neutral", "against")}
        per_sym[sym] = row
        print(f"  {sym:<6} with={row['with']} neutral={row['neutral']} "
              f"against={row['against']}")
    out["per_symbol_trial"] = per_sym

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
