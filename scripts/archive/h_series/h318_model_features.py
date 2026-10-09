# -*- coding: utf-8 -*-
"""H318 反转模型候选特征评估（研究，只读，不改任何生产文件/参数）。

# 目的：为 h300 反转 learner（线上 r60+vwap60，r60 权重≈−0.41）评估候选特征：
    ofi15s_all 15s 桶主动买卖失衡（跨交易所求和；与线上 OFI 门同口径，无交易所过滤）
    ofi15s_adx 15s 桶主动买卖失衡（仅 asterdex 交易所，本策略实际成交场）
    depth_imb5 盘口前 5 档 qty 失衡 (bid_qty−ask_qty)/(bid_qty+ask_qty)，asterdex_depth_snapshots 最近一次快照
    spread_bp  即时点差 (ask−bid)/mid×1e4，asterdex_book_ticker
    r15        过去 15s 收益（bp，1s 网格）
    r180       过去 180s 收益（bp，1s 网格）
    vol30      过去 30s 每秒 |ret| 之和（bp，h284 σ90 同款构造，30s 窗）
# 标签：未来 60s mid 收益（bp），与 h300 完全一致（不做 −sign(r60)×fwd 变换；
#       负 IC = 反转方向可用）。事件：book_ticker 15s 桶网格，h282 逐事件配对原则
#       （每事件一个 dict 一条记录，杜绝特征/标签错位 → 防 IC 符号翻转）。
# 评估：W1 = train（--hours 窗，--ago 偏移），W0 = eval（时间上严格靠前的上一窗）；
#       ridge（l2 ∈ {0,1,10}，h300 同款标准化/求解）基座 r60+vwap60，候选逐个加入，
#       报 oos IC（Pearson/Spearman）、Δoos（同子集基线对照）、权重向量（新特征符号与幅度）。
# 取数纪律：单币顺序、每表一次查询、断线重试一次、深度统计全部 SQL 端聚合（LATERAL 索引
#           求每 15s 桶最后快照，前 5 档直接下标，禁止整表 jsonb 拉回 Python）。
# 已知数据约束：asterdex_* 表仅保留 ~9 天（数据起点 1789540926），--ago 168 的评估窗
#           只能覆盖 ~2 天，脚本会如实打印覆盖率；建议补跑 --hours 96 做全覆盖 OOS。

# 用法

    python scripts/h318_model_features.py --hours 168            # 本周训练，前周评估
    python scripts/h318_model_features.py --hours 168 --ago 168  # 前周训练，前前周评估（覆盖率低）
    python scripts/h318_model_features.py --hours 96             # 96h 训练，前 96h 全覆盖评估
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "research_l1" / "out"
CUR = ["SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"]
BARE = {"SOLUSDT": "SOL", "DOGEUSDT": "DOGE", "ETHUSDT": "ETH", "BNBUSDT": "BNB"}
L2_GRID = [0.0, 1.0, 10.0]
BASE_FEATS = ["r60", "vwap60"]
CAND_FEATS = ["ofi15s_all", "ofi15s_adx", "depth_imb5", "spread_bp", "r15", "r180", "vol30"]
# [h319] 线上 runner 可计算口径（15s 快照 cadence / asterdex volume OFI）：
# 与训练保持 train/serve 一致的可部署特征
LIVE_FEATS = ["r60_15s", "vol30_15s", "ofi15s_live"]
ALL_FEATS = BASE_FEATS + CAND_FEATS + LIVE_FEATS


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def q_once(sql, params):
    """单连接查询；连接被断时 sleep 后重试一次（本脚本全程串行，无并发）。"""
    import psycopg

    for attempt in (1, 2):
        try:
            with psycopg.connect(market_dsn(), autocommit=True, connect_timeout=30,
                                 options="-c statement_timeout=600000") as c:
                with c.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
        except psycopg.OperationalError as e:
            print(f"  [warn] 查询断开（第 {attempt} 次尝试）: {e}", flush=True)
            if attempt == 1:
                time.sleep(3)
            else:
                raise


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0
    r = sxy / (sxx * syy) ** 0.5
    t = r * ((n - 2) / (1 - r * r)) ** 0.5 if abs(r) < 1 else float("inf")
    return r, t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0, help="训练窗小时数")
    ap.add_argument("--ago", type=float, default=0.0, help="窗口前移小时数（OOS 评估用前窗）")
    ap.add_argument("--eval-ahead", type=float, default=0.0,
                    help=">0：评估窗置于训练窗之后（walk-forward 末段验证），宽为该小时数，截至 now-ago")
    a = ap.parse_args()

    import numpy as np
    from scipy.stats import spearmanr

    now_s = int(time.time())
    if a.eval_ahead > 0:
        # walk-forward：eval=[now-ago-E, now-ago)，train=[now-ago-E-H, now-ago-E)
        w0e = now_s - int(a.ago * 3600)
        w0s = w0e - int(a.eval_ahead * 3600)
        w1e = w0s
        w1s = w1e - int(a.hours * 3600)
    else:
        w1e = now_s - int(a.ago * 3600)
        w1s = w1e - int(a.hours * 3600)
        w0s = w1s - int(a.hours * 3600)
        w0e = w1s
    lo_ms, hi_ms = min(w0s, w1s) * 1000, max(w0e, w1e) * 1000
    print(f"H318 候选特征评估  hours={a.hours} ago={a.ago} eval_ahead={a.eval_ahead}")
    print(f"  窗口: eval W0=[{w0s},{w0e})  train W1=[{w1s},{w1e})", flush=True)

    # ---------------- 取数（每币顺序、每表一次、断线重试一次） ----------------
    data = {}
    for sym in CUR:
        print(f"\n-- {sym} 取数 --", flush=True)
        # 1) book_ticker 1s 桶（最新一条 quote）
        rows = q_once("""
            SELECT (event_ts_ms/1000)::bigint AS bucket,
                   (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                   (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
            FROM asterdex_book_ticker
            WHERE event_ts_ms >= %s AND event_ts_ms < %s
              AND symbol = %s AND bid_px > 0 AND ask_px > bid_px
            GROUP BY bucket
        """, (lo_ms, hi_ms, sym))
        ts1 = [int(r[0]) for r in rows]
        px1 = [(float(r[1]) + float(r[2])) / 2.0 for r in rows]
        sp1 = [(float(r[2]) - float(r[1])) / ((float(r[2]) + float(r[1])) / 2.0) * 1e4
               for r in rows]
        print(f"  book 1s: {len(ts1)} 点", flush=True)
        # 2) asterdex_trades 1s 桶 VWAP（sum(px*qty), sum(qty)）
        rows = q_once("""
            SELECT (event_ts_ms/1000)::bigint AS b, sum(price*qty), sum(qty)
            FROM asterdex_trades
            WHERE event_ts_ms >= %s AND event_ts_ms < %s AND symbol = %s
            GROUP BY b ORDER BY b
        """, (lo_ms, hi_ms, sym))
        tb = [int(r[0]) for r in rows]
        pnum = [0.0]
        pden = [0.0]
        for r in rows:
            pnum.append(pnum[-1] + float(r[1]))
            pden.append(pden[-1] + float(r[2]))
        print(f"  trades 1s 桶: {len(tb)}", flush=True)
        # 3) market_trades_aggregated 15s OFI（裸符号；按交易所分组，同时出两版：
        #    all=跨交易所求和【与线上 OFI 门同口径】，adx=仅 asterdex【本策略成交场】）
        rows = q_once("""
            SELECT exchange, timestamp,
                   sum(COALESCE(taker_buy_notional, 0)),
                   sum(COALESCE(taker_sell_notional, 0)),
                   sum(COALESCE(taker_buy_volume, 0)),
                   sum(COALESCE(taker_sell_volume, 0))
            FROM market_trades_aggregated
            WHERE timestamp >= %s AND timestamp < %s AND symbol = %s
            GROUP BY exchange, timestamp
        """, (lo_ms, hi_ms, BARE[sym]))
        ofi_adx = {}
        ofi_acc = {}
        ofi_live = {}
        for exch, ts_ms, buy, sell, bv, sv in rows:
            bkey = int(ts_ms) // 15000
            tot = float(buy) + float(sell)
            ofi_v = (float(buy) - float(sell)) / tot if tot > 0 else 0.0
            if exch == "asterdex":
                ofi_adx[bkey] = ofi_v
                totv = float(bv) + float(sv)
                ofi_live[bkey] = (float(bv) - float(sv)) / totv if totv > 0 else 0.0
            cur = ofi_acc.get(bkey)
            ofi_acc[bkey] = (cur[0] + float(buy), cur[1] + float(sell)) if cur else (float(buy), float(sell))
        ofi_all = {}
        for bkey, (bu, se) in ofi_acc.items():
            tot = bu + se
            ofi_all[bkey] = (bu - se) / tot if tot > 0 else 0.0
        print(f"  mta OFI 桶: all={len(ofi_all)} adx={len(ofi_adx)} live(vol)={len(ofi_live)}", flush=True)
        # 4) asterdex_depth_snapshots：每 15s 桶最后一条快照的前 5 档 qty（SQL 端聚合）
        rows = q_once("""
            SELECT g.b, d.bq, d.aq
            FROM generate_series(%s, %s - 1) AS g(b)
            LEFT JOIN LATERAL (
                SELECT
                  COALESCE((bids->0->>1)::float8,0)+COALESCE((bids->1->>1)::float8,0)
                 +COALESCE((bids->2->>1)::float8,0)+COALESCE((bids->3->>1)::float8,0)
                 +COALESCE((bids->4->>1)::float8,0) AS bq,
                  COALESCE((asks->0->>1)::float8,0)+COALESCE((asks->1->>1)::float8,0)
                 +COALESCE((asks->2->>1)::float8,0)+COALESCE((asks->3->>1)::float8,0)
                 +COALESCE((asks->4->>1)::float8,0) AS aq
                FROM asterdex_depth_snapshots
                WHERE symbol = %s AND event_ts_ms >= %s
                  AND event_ts_ms < (g.b::bigint + 1) * 15000
                ORDER BY event_ts_ms DESC LIMIT 1
            ) d ON true
        """, (w0s // 15, max(w0e, w1e) // 15, sym, lo_ms))
        dep = {int(r[0]): (float(r[1]), float(r[2])) for r in rows if r[1] is not None}
        print(f"  depth 15s 桶: {len(dep)}", flush=True)
        m15 = {}
        for b2 in range((ts1[0] + 14) // 15, ts1[-1] // 15 + 1):
            jj = bisect_right(ts1, b2 * 15) - 1
            if jj >= 0 and b2 * 15 - ts1[jj] <= 15:
                m15[b2] = px1[jj]
        data[sym] = {"ts1": ts1, "px1": px1, "sp1": sp1,
                     "tb": tb, "pnum": pnum, "pden": pden,
                     "ofi_all": ofi_all, "ofi_adx": ofi_adx, "ofi_live": ofi_live,
                     "dep": dep, "m15": m15}

    # ---------------- 事件构造（15s 桶网格，逐事件单 dict 配对） ----------------
    events = {"W0": [], "W1": []}
    for sym in CUR:
        d = data[sym]
        ts1, px1, sp1 = d["ts1"], d["px1"], d["sp1"]
        tb, pnum, pden = d["tb"], d["pnum"], d["pden"]
        ofi_all, ofi_adx, ofi_live, dep, m15 = d["ofi_all"], d["ofi_adx"], d["ofi_live"], d["dep"], d["m15"]
        for wname, (ws, we) in (("W0", (w0s, w0e)), ("W1", (w1s, w1e))):
            b0 = (ws + 14) // 15
            b1 = we // 15
            for b in range(b0, b1):
                t = b * 15
                ie = bisect_right(ts1, t) - 1
                if ie < 0 or t - ts1[ie] > 15:
                    continue
                # 标签：未来 60s mid 收益（与 h300 一致，不做符号变换）
                tf = (b + 4) * 15
                ff = bisect_right(ts1, tf) - 1
                if ff < 0 or tf - ts1[ff] > 15 or ts1[ff] - ts1[ie] < 54:
                    continue
                y = (px1[ff] - px1[ie]) / px1[ie] * 1e4
                # r60（1s 网格，h300 past(60) 口径）
                j = bisect_left(ts1, t - 60)
                if j > ie or t - ts1[j] < 54:
                    continue
                r60 = (px1[ie] - px1[j]) / px1[j] * 1e4
                # vwap60（h300 口径：60s 成交 VWAP 偏差）
                i2 = bisect_right(tb, t)
                i1 = bisect_left(tb, t - 60)
                den = pden[i2] - pden[i1]
                vwap = (pnum[i2] - pnum[i1]) / den if den > 0 else px1[ie]
                vwap60 = (px1[ie] - vwap) / vwap * 1e4
                # vol30（h284 σ90 同款：过去 30s 每秒 |ret| 和）
                i30 = bisect_left(ts1, t - 30)
                vol30 = None
                if ie - i30 >= 10:
                    vol30 = sum(abs((px1[k + 1] - px1[k]) / px1[k]) * 1e4
                                for k in range(i30, ie))
                # r15 / r180（1s 网格，h282 past(k) 口径，覆盖 ≥0.9k）
                def past_ret(k, min_cov):
                    jk = bisect_left(ts1, t - k)
                    if jk > ie or t - ts1[jk] < min_cov:
                        return None
                    return (px1[ie] - px1[jk]) / px1[jk] * 1e4
                r15 = past_ret(15, 13.5)
                r180 = past_ret(180, 162.0)
                # ofi15s（事件前一个完整 15s 桶；dict 键 = ts_ms//15000 即桶序号）
                ofi15s_all = ofi_all.get(b - 1)
                ofi15s_adx = ofi_adx.get(b - 1)
                # [h319] 线上 runner 可计算口径（15s 快照 cadence + asterdex volume OFI）
                _m0 = m15.get(b)
                _m1 = m15.get(b - 4)
                _m2 = m15.get(b - 1)
                _m3 = m15.get(b - 2)
                r60_15s = ((px1[ie] - _m1) / _m1 * 1e4) if (_m0 is not None and _m1 and _m1 > 0) else None
                vol30_15s = None
                if _m0 and _m2 and _m3 and _m0 > 0 and _m2 > 0 and _m3 > 0:
                    vol30_15s = (abs(_m0 - _m2) / _m2 + abs(_m2 - _m3) / _m3) * 1e4
                ofi15s_live = ofi_live.get(b - 1)
                # depth_imb5（事件前一桶的最后一次快照，前 5 档 qty 失衡）
                dp = dep.get(b - 1)
                depth_imb5 = None
                if dp is not None and dp[0] + dp[1] > 0:
                    depth_imb5 = (dp[0] - dp[1]) / (dp[0] + dp[1])
                events[wname].append({
                    "ts": t, "sym": sym, "y": y, "r60": r60, "vwap60": vwap60,
                    "ofi15s_all": ofi15s_all, "ofi15s_adx": ofi15s_adx,
                    "depth_imb5": depth_imb5, "spread_bp": sp1[ie],
                    "r15": r15, "r180": r180, "vol30": vol30,
                    "r60_15s": r60_15s, "vol30_15s": vol30_15s, "ofi15s_live": ofi15s_live})

    n0, n1 = len(events["W0"]), len(events["W1"])
    print(f"\n事件: W0(eval)={n0}  W1(train)={n1}", flush=True)
    for wname in ("W0", "W1"):
        ev = events[wname]
        if ev:
            span = (max(r["ts"] for r in ev) - min(r["ts"] for r in ev)) / 3600
            cov = span / a.hours * 100
            warn = "" if cov >= 95 else "  ⚠ 覆盖率不足（数据保留窗口限制），结论按有效样本加权"
            print(f"  {wname}: ts {min(r['ts'] for r in ev)}..{max(r['ts'] for r in ev)}"
                  f"  跨度 {span:.1f}h  覆盖率 {cov:.1f}%{warn}", flush=True)
        else:
            print(f"  {wname}: 无事件（数据缺失）", flush=True)

    result = {"args": {"hours": a.hours, "ago": a.ago},
              "windows": {"W0": [w0s, w0e], "W1": [w1s, w1e]},
              "n_events": {"W0": n0, "W1": n1},
              "as_of": datetime.now(timezone.utc).isoformat()}

    # ---------------- 特征 IC（Pearson + Spearman，两窗口） ----------------
    print("\n== 特征 IC（标签=未来 60s mid 收益 bp；负 IC = 反转方向） ==")
    ic_out = {}
    for wname in ("W0", "W1"):
        ev = events[wname]
        print(f"\n  [{wname} n={len(ev)}]")
        print(f"  {'特征':>11} {'Pearson':>9} {'t':>7} {'Spearman':>9} {'n':>7}   覆盖率")
        ic_out[wname] = {}
        for f in ALL_FEATS:
            sub = [(r[f], r["y"]) for r in ev if r[f] is not None]
            if len(sub) < 30:
                print(f"  {f:>11}  {'-':>9} {'-':>7} {'-':>9} {len(sub):>7}   {len(sub)/max(len(ev),1)*100:4.1f}%")
                continue
            xs = [s[0] for s in sub]
            ys = [s[1] for s in sub]
            r, t = pearson(xs, ys)
            try:
                sp = float(spearmanr(xs, ys).statistic)
            except Exception:
                sp = float("nan")
            ic_out[wname][f] = {"pearson": round(r, 5), "t": round(t, 2),
                                "spearman": round(sp, 5), "n": len(sub)}
            print(f"  {f:>11} {r:>+9.4f} {t:>7.1f} {sp:>+9.4f} {len(sub):>7}   {len(sub)/max(len(ev),1)*100:4.1f}%")
    result["ic"] = ic_out

    # 候选与 r60 的相关（共线性参考）
    print("\n== 候选 vs r60 相关（W1） ==")
    ev1 = events["W1"]
    corr_r60 = {}
    for f in CAND_FEATS:
        sub = [(r[f], r["r60"]) for r in ev1 if r[f] is not None]
        if len(sub) >= 30:
            r, _ = pearson([s[0] for s in sub], [s[1] for s in sub])
            corr_r60[f] = round(r, 4)
            print(f"  {f:>11} corr(r60) = {r:+.4f}  (n={len(sub)})")
    result["corr_with_r60"] = corr_r60

    # ---------------- ridge：基座 r60+vwap60，候选逐个加入 ----------------
    print("\n== ridge（h300 同款标准化+l2 网格 {0,1,10}；train=W1, eval=W0 严格靠前） ==")
    ridge_out = []
    ev0, ev1 = events["W0"], events["W1"]

    def run_combo(combo_name, feats, tr_rows, va_rows):
        """在同一完整子集上训练；返回每 λ 的 (train_corr, oos_corr, oos_spear, beta)。"""
        Xtr = np.array([[r[f] for f in feats] for r in tr_rows], dtype=float)
        Ytr = np.array([r["y"] for r in tr_rows], dtype=float)
        Xva = np.array([[r[f] for f in feats] for r in va_rows], dtype=float)
        Yva = np.array([r["y"] for r in va_rows], dtype=float)
        mu = Xtr.mean(0)
        sd = Xtr.std(0) + 1e-9
        Xs = (Xtr - mu) / sd
        Vs = (Xva - mu) / sd
        rows = []
        for lam in L2_GRID:
            A = Xs.T @ Xs + lam * np.eye(Xs.shape[1])
            b = Xs.T @ Ytr
            beta = np.linalg.solve(A, b)
            pred_tr = Xs @ beta
            pred_va = Vs @ beta
            ic_tr, _ = pearson(list(pred_tr), list(Ytr))
            ic_va, _ = pearson(list(pred_va), list(Yva))
            try:
                sp_va = float(spearmanr(list(pred_va), list(Yva)).statistic)
            except Exception:
                sp_va = float("nan")
            rows.append({"lam": lam, "train_corr": round(float(ic_tr), 5),
                         "oos_corr": round(float(ic_va), 5),
                         "oos_spear": round(float(sp_va), 5),
                         "beta": {f: round(float(w), 6) for f, w in zip(feats, beta)}})
        return rows

    if n1 < 200:
        print("  [warn] W1 训练窗口事件不足 200，跳过 ridge 评估", flush=True)
    else:
        combos = [("base", BASE_FEATS)] + [(f"base+{c}", BASE_FEATS + [c]) for c in CAND_FEATS] \
                 + [("base+all7", BASE_FEATS + CAND_FEATS)] \
                 + [("live_r60", ["r60_15s"]),
                    ("live_r60+vol30", ["r60_15s", "vol30_15s"]),
                    ("live_full", ["r60_15s", "vol30_15s", "ofi15s_live"])]
        for combo_name, feats in combos:
            tr_rows = [r for r in ev1 if all(r[f] is not None for f in feats)]
            va_rows = [r for r in ev0 if all(r[f] is not None for f in feats)]
            if len(tr_rows) < 200:
                print(f"  {combo_name}: 训练子集 {len(tr_rows)} 不足，跳过", flush=True)
                continue
            if len(va_rows) < 100:
                print(f"  {combo_name}: 评估子集 {len(va_rows)} 不足（数据缺失），跳过 OOS", flush=True)
                continue
            base_tr = [r for r in ev1 if all(r[f] is not None for f in BASE_FEATS)]
            base_va = [r for r in ev0 if all(r[f] is not None for f in BASE_FEATS)]
            res_combo = run_combo(combo_name, feats, tr_rows, va_rows)
            res_base_full = run_combo("base_full", BASE_FEATS, base_tr, base_va)
            # 同子集基线（公平 Δ：与 combo 相同的行子集）
            res_base_sub = None
            if combo_name != "base":
                res_base_sub = run_combo("base_sub", BASE_FEATS, tr_rows, va_rows)
            new_feat = feats[-1] if len(feats) == 3 else ("all7" if len(feats) > 3 else "-")
            print(f"\n  [{combo_name}] n_tr={len(tr_rows)} n_va={len(va_rows)}")
            print(f"  {'λ':>4} {'trainIC':>9} {'oosIC':>9} {'oosSpear':>9} "
                  f"{'base_full':>10} {'base_sub':>10} {'Δoos(sub)':>10} "
                  f"{'新特征w':>9} {'r60w':>8} {'vwap60w':>8}")
            for k, lam in enumerate(L2_GRID):
                rc = res_combo[k]
                bf = res_base_full[k]
                bs = res_base_sub[k] if res_base_sub else None
                delta = round(rc["oos_corr"] - bs["oos_corr"], 5) if bs else None
                nw = rc["beta"].get(new_feat, float("nan")) if new_feat != "-" else None
                if nw is None:
                    nws = "-"
                else:
                    nws = f"{nw:+.4f}"
                delta_s = f"{delta:+.5f}" if delta is not None else "-"
                _r60w = rc["beta"].get("r60")
                _vwapw = rc["beta"].get("vwap60")
                print(f"  {lam:>4.0f} {rc['train_corr']:>+9.4f} {rc['oos_corr']:>+9.4f} "
                      f"{rc['oos_spear']:>+9.4f} {bf['oos_corr']:>+10.4f} "
                      f"{bs['oos_corr'] if bs else 0.0:>+10.4f} {delta_s:>10} "
                      f"{nws:>9} "
                      f"{(_r60w if _r60w is not None else float('nan')):>+8.3f} "
                      f"{(_vwapw if _vwapw is not None else float('nan')):>+8.3f}")
            # 最佳 λ（按 oos corr）
            best = max(res_combo, key=lambda x: x["oos_corr"])
            best_delta = None
            if res_base_sub:
                bs_best = res_base_sub[L2_GRID.index(best["lam"])]
                best_delta = round(best["oos_corr"] - bs_best["oos_corr"], 5)
            ridge_out.append({"combo": combo_name, "n_tr": len(tr_rows), "n_va": len(va_rows),
                              "best_lam": best["lam"], "best_oos": best["oos_corr"],
                              "best_delta_sub": best_delta, "per_lam": res_combo})
            if new_feat != "-" and best["beta"].get(new_feat) is not None:
                nw = best["beta"].get(new_feat)
                print(f"    → 最佳λ={best['lam']:.0f} oosIC={best['oos_corr']:+.4f} "
                      f"Δoos(同子集)={best_delta} 新特征w={nw:+.4f}")
            elif new_feat == "all7":
                w5 = {f: best["beta"].get(f) for f in CAND_FEATS}
                print(f"    → 最佳λ={best['lam']:.0f} oosIC={best['oos_corr']:+.4f} "
                      f"Δoos(同子集)={best_delta} 候选权重 {w5}")
            else:
                print(f"    → 最佳λ={best['lam']:.0f} oosIC={best['oos_corr']:+.4f} "
                      f"Δoos(同子集)={best_delta}")

    result["ridge"] = ridge_out

    # ---------------- 结论摘要 ----------------
    print("\n== 摘要 ==")
    if ridge_out:
        base_best = next((x for x in ridge_out if x["combo"] == "base"), None)
        if base_best:
            print(f"  基座 r60+vwap60 最佳 oos IC = {base_best['best_oos']:+.4f}"
                  f"（λ={base_best['best_lam']:.0f}）；对照：线上 r60 权重 ≈ −0.41")
        for x in ridge_out:
            if x["combo"] != "base":
                flag = "采纳?" if (x["best_delta_sub"] or 0) > 0.003 else "存疑/拒绝"
                print(f"  {x['combo']:>16}: 最佳 oos IC {x['best_oos']:+.4f}  "
                      f"Δ(同子集) {x['best_delta_sub']}  → {flag}")
    else:
        print("  （ridge 未运行：训练窗口数据不足）")

    out_path = OUT_DIR / f"h318_model_features_a{int(a.ago)}_h{int(a.hours)}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
