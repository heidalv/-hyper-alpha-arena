# -*- coding: utf-8 -*-
"""H388 #17 影子测验：保本/移动止损 vs 现行止损，在 #2 时代全部止损腿上重演。

方法：对每笔 stop_loss 腿，取入场腿的 (ts, fill_px, side)，沿该币 1s 中价路径
（asterdex_book_ticker 逐秒 mid）模拟两种规则：
  A 现行：tp +30bp / stop −40bp（先到先出）
  B 保本尾随（#17 设计）：初始 stop −40；MFE≥+20bp ⇒ stop 抬至 −5（保本）；
    此后每再 +10bp 抬 10bp（MFE +30 ⇒ +10、+40 ⇒ +20 …）；tp +30 不变。
输出：每腿两规则各自的实现 bp、总回收差，以及"B 会把哪些腿从深亏变平/小赚"。
（中价口径无 taker 滑点——B 相对 A 的**增量**才是结论，水平值仅供参考。）

用法: python scripts/h388_breakeven_shadow.py
"""
from __future__ import annotations

import bisect
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h388_breakeven_shadow.json"

TP, STOP0, BE_AT = 30.0, 40.0, 20.0


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
    import json

    import psycopg

    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            # CTE + 窗口函数：每 position 的首腿=入场（避免 LATERAL 逐行子查询）
            cur.execute("""
                WITH legs AS (
                  SELECT symbol, position_id, ts, meta_json,
                         ROW_NUMBER() OVER (PARTITION BY position_id ORDER BY ts) AS rn
                  FROM lane_ledger
                  WHERE lane_id='mm_asterdex'
                    AND ts > '2026-09-27 13:00:00+08'::timestamptz
                ),
                entries AS (SELECT * FROM legs WHERE rn = 1),
                stops AS (
                  SELECT * FROM lane_ledger
                  WHERE lane_id='mm_asterdex'
                    AND meta_json->>'exit_path' LIKE 'stop_loss%'
                    AND ts > '2026-09-27 13:00:00+08'::timestamptz
                )
                SELECT s.symbol, s.position_id,
                       e.meta_json->>'side' AS side,
                       (e.meta_json->>'fill_px')::float8 AS entry_px,
                       e.ts AS entry_ts, s.ts AS exit_ts, s.net_bp
                FROM stops s JOIN entries e ON e.position_id = s.position_id
                ORDER BY s.ts
            """)
            legs = cur.fetchall()
    print(f"止损腿 {len(legs)} 笔", flush=True)

    # 需要的币种 1s 中价（从最早入场前 2 分钟到最晚出场后 1 分钟）
    syms = sorted({l[0] for l in legs})
    mids_by_sym = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
        with c.cursor() as cur:
            for sym in syms:
                t0 = min(l[4].timestamp() for l in legs if l[0] == sym) - 120
                t1 = max(l[5].timestamp() for l in legs if l[0] == sym) + 60
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s
                      AND event_ts_ms >= %s::bigint*1000 AND event_ts_ms <= %s::bigint*1000
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (sym + "USDT", int(t0), int(t1)))
                recs = cur.fetchall()
                mids_by_sym[sym] = ([(int(r[0]), (float(r[1]) + float(r[2])) / 2.0)
                                    for r in recs])

    def simulate(ts_list, mids, i0, entry_px, side, breakeven: bool):
        """B 规则（尾随）：初始 stop −40；MFE≥+20 ⇒ −5；每再 +10 抬 10；
        无 TP 分支（TP 的 maker 宽限在快行情里不成交，实际已证——见 h388 结论）。
        返回 (exit_bp, exit_reason, max_mfe_bp)。"""
        mfe = 0.0
        stop_line = -STOP0
        for i in range(i0, len(mids)):
            if ts_list[i] < ts_list[i0 - 1]:
                continue
            ret = (mids[i] - entry_px) / entry_px * 1e4 * side
            if ret > mfe:
                mfe = ret
            if breakeven and mfe >= BE_AT:
                stop_line = max(stop_line, -5.0 + 10.0 * ((mfe - BE_AT) // 10.0))
            if ret <= stop_line:
                return ret, ("trail" if stop_line > -STOP0 else "stop"), mfe
            if i - (i0 - 1) > 3600 * 8:
                return ret, "timeout", mfe
        return (mids[-1] - entry_px) / entry_px * 1e4 * side, "end", mfe

    rows = []
    tot_a = tot_b = 0.0
    n_saved = 0
    for sym, pid, side, entry_px, entry_ts, exit_ts, net_bp in legs:
        series = mids_by_sym.get(sym)
        if not series:
            continue
        ts_list = [t for t, _ in series]
        mid_list = [m for _, m in series]
        i0 = bisect.bisect_left(ts_list, int(entry_ts.timestamp()))
        if i0 >= len(mid_list) - 1:
            continue
        s = 1.0 if side == "buy" else -1.0
        a_bp = float(net_bp or 0.0)                 # 基线 = 账本已实现
        b_bp, b_r, b_mfe = simulate(ts_list, mid_list, i0 + 1, entry_px, s, True)
        tot_a += a_bp
        tot_b += b_bp
        if b_bp - a_bp > 5:
            n_saved += 1
        rows.append({"sym": sym, "side": side, "entry": str(entry_ts)[11:19],
                     "mfe_bp": round(b_mfe, 1),
                     "A_bp": round(a_bp, 1), "A_exit": "ledger",
                     "B_bp": round(b_bp, 1), "B_exit": b_r,
                     "delta": round(b_bp - a_bp, 1)})

    print(f"\n{'币':<6} {'方向':<5} {'入场':<8} {'MFE':>7} {'现行A':>8} {'尾随B':>8} {'Δ':>7}")
    for r in rows:
        print(f"{r['sym']:<6} {r['side']:<5} {r['entry']:<8} {r['mfe_bp']:>+7.1f} "
              f"{r['A_bp']:>+8.1f} {r['B_bp']:>+8.1f} {r['delta']:>+7.1f}")
    n = len(rows)
    print(f"\n合计：现行 A = {tot_a:+.1f}bp ／ 尾随 B = {tot_b:+.1f}bp "
          f"⇒ 尾随净回收 {tot_b - tot_a:+.1f}bp（{n} 腿，其中 {n_saved} 腿 Δ>+5bp）")
    OUT.write_text(json.dumps({"n": n, "tot_A": round(tot_a, 1), "tot_B": round(tot_b, 1),
                               "recovered": round(tot_b - tot_a, 1), "saved_legs": n_saved,
                               "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
