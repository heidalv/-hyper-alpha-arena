"""MR 入场分桶分析（2026-08-28）：哪些入场特征桶有正期望。
把 MR 成交与 scalp_signal_log 的 features（score/rsi/振幅/位置）对齐，
分桶统计实现结果（tp/sl/超时/保本 + 用 exit_ret 算期望）。
"""
import datetime
import json
import sys

sys.path.insert(0, ".")

import numpy as np
import psycopg2

MAIN = psycopg2.connect(host="127.0.0.1", port=5432, dbname="alpha_arena",
                        user="laobao", password="alpha_pass")


def main():
    cur = MAIN.cursor()
    cur.execute("SET app.is_admin='on'")
    # 信号日志（features_json 带 score/rsi 等）
    cur.execute("""
        SELECT symbol, signal_ts, features_json FROM scalp_signal_log
        WHERE signal_ts > extract(epoch from timestamp '2026-06-30')::bigint
    """)
    sigs = {}
    for s, ts, fj in cur.fetchall():
        try:
            d = json.loads(fj) if fj else {}
        except Exception:
            d = {}
        sigs.setdefault(s, []).append((int(ts), d))

    cur.execute("""
        SELECT id, symbol, side, opened_at, close_reason, partial_realized_pnl, partial_fee_paid
        FROM paper_positions
        WHERE trade_nature='scalp' AND strategy_id LIKE 'scalp_mr_%' AND status='closed'
        ORDER BY opened_at
    """)
    trades = cur.fetchall()

    rows = []
    for t in trades:
        pid, sym, side, opened, reason, pnl, fee = t
        t0 = int(opened.timestamp())
        best = None
        for ts, d in sigs.get(sym, []):
            if abs(ts - t0) <= 600:
                best = d
                break
        if best is None:
            continue
        score = float(best.get("factor_score") or best.get("score") or 0)
        rsi = float(best.get("rsi") or 50)
        amp = float(best.get("amplitude_pct") or 0)
        pos = float(best.get("range_position") or 0.5)
        win = 1 if reason in ("tp", "breakeven_tp", "hold_timeout_review") else 0
        net = float(pnl or 0) - float(fee or 0)
        rows.append((pid, score, rsi, amp, pos, win, net, reason))

    print("joined:", len(rows), "/", len(trades))
    if not rows:
        return

    def bucket_report(key_fn, key_name, edges):
        print("\n== 分桶 by %s ==" % key_name)
        for lo, hi in zip(edges[:-1], edges[1:]):
            sel = [r for r in rows if lo <= key_fn(r) < hi]
            if not sel:
                continue
            n = len(sel)
            w = sum(r[5] for r in sel)
            ev = sum(r[6] for r in sel) / n
            tps = sum(1 for r in sel if r[7] == "tp")
            sls = sum(1 for r in sel if r[7] == "sl")
            print("  %5.1f-%5.1f: n=%4d win=%.1f%% net_mean=%+.4f tp=%d sl=%d"
                  % (lo, hi, n, 100 * w / n, ev, tps, sls))

    bucket_report(lambda r: r[1], "score", [0, 55, 65, 75, 85, 101])
    bucket_report(lambda r: r[2], "rsi", [0, 20, 25, 30, 40, 101])
    bucket_report(lambda r: r[3], "amp_pct", [0, 0.8, 1.2, 1.6, 2.2, 99])
    bucket_report(lambda r: r[4], "range_pos", [-1, 0.05, 0.12, 0.2, 0.5, 2])
    # 方向分开
    cur.execute("SELECT DISTINCT side, count(*) FROM paper_positions WHERE trade_nature='scalp' AND strategy_id LIKE 'scalp_mr_%' GROUP BY side")
    print("\nside dist:", dict(cur.fetchall()))


if __name__ == "__main__":
    main()
