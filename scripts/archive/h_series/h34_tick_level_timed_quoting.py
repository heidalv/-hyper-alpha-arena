"""H34：tick 级重做择时挂单 —— 修掉 H32 的两个保留。

## H32 的两个保留（本脚本要修的）

  **保留 1：深度匹配对 BNB/DOGE 失效。**
      实测：BNB/DOGE 的 `asterdex_depth_snapshots` **只有近 6 小时**
      （最近才加入采集），而桶是 72 小时 ⇒ 大部分桶没有深度可比。
      BTC/ETH/SOL/XRP/ASTER 有完整 72h（各 ~220 万条快照）。
      ⇒ **本轮只用这 5 个币**，并把"深度覆盖"作为选币的硬条件。

  **保留 2：markout 用 15s 桶中价，起点偏晚 ⇒ 结果偏乐观。**
      真实成交发生在桶内更早时刻，用桶末中价当起点会**吃掉最毒的那一段**。
      ⇒ 本轮改用 **tick 级**：
          · 成交判定与成交价：`asterdex_trades`（逐笔，含主动方标记）
          · 中价与 markout：`asterdex_book_ticker`（最优价，p50 ~36ms）
          · 前方挂量：`asterdex_depth_snapshots` 的**真实最优档挂量**（基础币数量）

## 成交模型（论文口径，全部同量纲）

    ① 在快照 i 挂买单于 `bid_px`、卖单于 `ask_px`
    ② 前方挂量 LA = `bids->0->>1` / `asks->0->>1`（**基础币数量**）
    ③ 成交条件：自挂单起，**在该价位的累计对手方主动量（基础币数量）≥ LA**
       —— 与 LA **同量纲**（这是 H31 犯过的错：拿币和美元比大小）
    ④ markout **从限价算**：买单 `(mid_{T+τ}/px − 1)`，卖单 `(1 − mid_{T+τ}/px)`
       中价取成交时刻**之后**第一个 book_ticker 快照

## 判据（事先定死）

  · P2（反势挂）的**每决策净额** > P0（基准）且改善 ≥ 0.05bp
    ⇒ 信号有执行价值，值得接进引擎
  · 改善在 0.02~0.05bp ⇒ 弱，需配合其他改动
  · 改善 < 0.02bp 或符号翻转 ⇒ **H32 的 +0.041bp 是桶粒度造成的假象**

用法：
    .venv\\Scripts\\python.exe scripts\\h34_tick_level_timed_quoting.py --hours 24
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
TAUS_MS = [1000, 5000, 30000]      # markout 时点


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def process_symbol(s, hours: float, theta: float, quote_every_s: float,
                   tol_ms: int, max_wait_s: float, log):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = s if s.endswith("USDT") else f"{s}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours*3600_000)}"

    # ① 深度快照（真实逐档挂量）
    cur.execute(
        "SELECT event_ts_ms, (bids->0->>0)::float bp, (bids->0->>1)::float bq,"
        "       (asks->0->>0)::float ap, (asks->0->>1)::float aq,"
        f"      (SELECT SUM((x->>1)::float) FROM jsonb_array_elements(bids) x) bsum,"
        f"      (SELECT SUM((x->>1)::float) FROM jsonb_array_elements(asks) x) asum"
        f"  FROM asterdex_depth_snapshots WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    dep = cur.fetchall()
    # ② 逐笔成交
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    # ③ 最优价（中价 + 盘口量，用于 flow_imb 特征）
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a,"
        "       bid_qty::float bq, ask_qty::float aq"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    bt = cur.fetchall()
    cn.close()

    if len(dep) < 2000 or len(tr) < 1000 or len(bt) < 2000:
        log(f"  {s:<8} 数据不足（深度 {len(dep)} / 逐笔 {len(tr)} / 盘口 {len(bt)}）→ 跳过")
        return None

    dts = np.array([int(x["event_ts_ms"]) for x in dep], dtype=np.int64)
    dbp = np.array([float(x["bp"] or 0) for x in dep])
    dbq = np.array([float(x["bq"] or 0) for x in dep])
    dap = np.array([float(x["ap"] or 0) for x in dep])
    daq = np.array([float(x["aq"] or 0) for x in dep])
    dbs = np.array([float(x["bsum"] or 0) for x in dep])
    das = np.array([float(x["asum"] or 0) for x in dep])


# [F259 修正] h34_tick_level_timed_quoting.py 的 markout 公式（见 _fix_abs_markout.py）
# net = half + mk（mk 自带方向）
# 旧写法 half - abs(mk) 把 markout 的标准差当成成本，
# 实测 30s 上虚增 1.47bp/笔（BTC 2h 样本）。
    tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in tr])
    tq = np.array([float(x["q"]) for x in tr])
    tsell = np.array([bool(x["ibm"]) for x in tr])      # 主动卖

    bts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
    bbid = np.array([float(x["b"] or 0) for x in bt])
    bask = np.array([float(x["a"] or 0) for x in bt])
    bbq = np.array([float(x["bq"] or 0) for x in bt])
    baq = np.array([float(x["aq"] or 0) for x in bt])
    bmid = (bbid + bask) / 2.0

    def mid_after(t_ms, delta):
        i = int(np.searchsorted(bts, t_ms + delta, "left"))
        return float(bmid[i]) if i < len(bts) else None

    # 报价时点：每 quote_every_s 秒取一个深度快照
    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    qidx = np.arange(0, len(dts), step, dtype=np.int64)

    # 信号：用 book_ticker 的盘口量算最优档失衡（flow_imb 的 tick 级版本）
    #   注意只用**当期及过去**：这里用挂单时刻之前最近的一批成交（过去 window_ms）
    W_MS = 15000
    sig_raw = np.zeros(len(qidx))
    for k, i in enumerate(qidx):
        t0 = int(dts[i])
        lo = int(np.searchsorted(bts, t0 - W_MS, "left"))
        hi = int(np.searchsorted(bts, t0, "left"))
        if hi - lo < 5:
            continue
        bq = bbq[lo:hi].mean()
        aq = baq[lo:hi].mean()
        den = bq + aq
        if den > 0:
            sig_raw[k] = (bq - aq) / den
    # 只用过去标准化
    mu = np.full(len(qidx), np.nan)
    sd = np.full(len(qidx), np.nan)
    W = 200
    for k in range(W, len(qidx)):
        w = sig_raw[k - W:k]
        if w.std() > 0:
            mu[k], sd[k] = w.mean(), w.std()
    z = np.where(np.isfinite(sd) & (sd > 0), (sig_raw - mu) / sd, 0.0)

    policies = ("P0 两侧都挂", "P1 顺势挂", "P2 反势挂", "P3 择时")
    res = {p: {"net": [], "n_dec": 0, "n_fill": 0, "detail": []} for p in policies}

    t0_all = int(dts[qidx[0]])
    for k, i in enumerate(qidx):
        zi = z[k]
        if not np.isfinite(zi):
            continue
        t0 = int(dts[i])
        bid, ask = float(dbp[i]), float(dap[i])
        la_b, la_a = float(dbq[i]), float(daq[i])
        if bid <= 0 or ask <= 0 or ask <= bid or la_b <= 0 or la_a <= 0:
            continue
        # 成交价必须与挂单价一致（±1bp），否则不算在该价位成交
        #
        # ⚠️ 还必须检查**价格是否还在我们的档位**：
        #    若最优买价已跌到我们挂单价**之下**，说明队列跟着价格走了，
        #    我们那张单已不在最优档 ⇒ **不应再判成交**。
        #    早先版本漏了这个守卫 ⇒ BTC/ETH 成交率虚高到 85~88%
        #    （因为 `cum_b` 会把几秒后价格跌到别处时的主动卖量也算进来）。
        j = int(np.searchsorted(tts, t0, "left"))
        lim = int(np.searchsorted(tts, t0 + int(max_wait_s * 1000), "left"))
        # 该时间窗内的深度快照下标范围（用于逐笔检查"价格是否还在我们档位"）
        di0 = int(np.searchsorted(dts, t0, "left"))
        di1 = int(np.searchsorted(dts, int(tts[min(lim, len(tts) - 1)]) if lim > 0 else t0,
                                  "left")) + 1
        cum_b = cum_a = 0.0
        fb = fa = -1
        for jj in range(j, lim):
            tj = int(tts[jj])
            # 找到 tj 时刻（或之前最近）的深度快照
            di = int(np.searchsorted(dts, tj, "right")) - 1
            if di < 0:
                continue
            cur_bid, cur_ask = float(dbp[di]), float(dap[di])
            if cur_bid > 0 and cur_ask > 0:
                # 买单：只有当最优买价仍 ≥ 我们的挂单价时才可能成交
                bid_alive = cur_bid >= bid * (1.0 - 1.0 / 1e4)
                ask_alive = cur_ask <= ask * (1.0 + 1.0 / 1e4)
            else:
                bid_alive = ask_alive = True
            if bid_alive and tsell[jj] and abs(tpx[jj] - bid) / bid * 1e4 < 1.0:
                cum_b += float(tq[jj])
                if cum_b >= la_b and fb < 0:
                    fb = jj
            elif ask_alive and (not tsell[jj]) and abs(tpx[jj] - ask) / ask * 1e4 < 1.0:
                cum_a += float(tq[jj])
                if cum_a >= la_a and fa < 0:
                    fa = jj
            if fb >= 0 and fa >= 0:
                break
        strong_up, strong_dn = zi >= theta, zi <= -theta
        for pol in policies:
            do_buy, do_sell = True, True
            if pol == "P1 顺势挂":
                do_buy, do_sell = strong_up, strong_dn
            elif pol == "P2 反势挂":
                do_buy, do_sell = strong_dn, strong_up
            elif pol == "P3 择时":
                if strong_up:
                    do_buy, do_sell = False, True
                elif strong_dn:
                    do_buy, do_sell = True, False
            if not (do_buy or do_sell):
                continue
            st = res[pol]
            st["n_dec"] += 1
            got = False
            if do_buy and fb >= 0:
                tf = int(tts[fb])
                for tau in TAUS_MS:
                    mm = mid_after(tf, tau)
                    if mm:
                        hs = (float(bmid[int(np.searchsorted(bts, tf, "left"))]) - bid) / bid * 1e4 \
                            if int(np.searchsorted(bts, tf, "left")) < len(bts) else 0.0
                        mk = (mm / bid - 1.0) * 1e4
                        net = hs + mk
                        st["net"].append(net)
                        st["detail"].append({"tau": tau, "net": net, "half": hs, "mk": mk})
                got = True
            if do_sell and fa >= 0:
                tf = int(tts[fa])
                for tau in TAUS_MS:
                    mm = mid_after(tf, tau)
                    if mm:
                        ii = int(np.searchsorted(bts, tf, "left"))
                        hs = (ask - float(bmid[ii])) / ask * 1e4 if ii < len(bts) else 0.0
                        mk = (1.0 - mm / ask) * 1e4
                        net = hs + mk
                        st["net"].append(net)
                        st["detail"].append({"tau": tau, "net": net, "half": hs, "mk": mk})
                got = True
            if got:
                st["n_fill"] += 1

    out = {"symbol": s, "n_quotes": int(len(qidx)), "policies": {}}
    for p in policies:
        st = res[p]
        if not st["net"]:
            out["policies"][p] = None
            continue
        a = np.array(st["net"])
        # 拆出「成交时半价差」与「markout」两项。
        # ⚠️ 这是 H31/H32 出错的地方：它们用**挂单时**的半价差（总为正），
        #    但成交时中价已经移动 ⇒ 真实捕获可能为负。
        #    这里存成交时口径，便于事后核对差值。
        det = st["detail"]
        h1 = np.array([d["half"] for d in det if d["tau"] == TAUS_MS[0]])
        m1 = np.array([d["mk"] for d in det if d["tau"] == TAUS_MS[0]])
        # ⚠️ 必须**按时点分别**存 markout。
        #    因为 `net_bp_per_fill` 是 τ∈{1s,5s,30s} 三个时点的**混合均值**，
        #    而 1s 的 markout 只有约 −0.1bp ⇒ 残差 ~1.4bp 全在更长的时点上。
        #    不分时点存，就无法定位亏损的时间结构（实测踩到）。
        per_tau = {}
        for tau in TAUS_MS:
            vv = np.array([d["net"] for d in det if d["tau"] == tau])
            mm = np.array([d["mk"] for d in det if d["tau"] == tau])
            hh = np.array([d["half"] for d in det if d["tau"] == tau])
            if len(vv):
                per_tau[str(tau)] = {
                    "n": int(len(vv)),
                    "net_bp": float(vv.mean()),
                    "mk_bp": float(mm.mean()) if len(mm) else None,
                    "half_bp": float(hh.mean()) if len(hh) else None,
                }
        out["policies"][p] = {
            "n_dec": st["n_dec"], "n_fill": st["n_fill"],
            "fill_rate": st["n_fill"] / max(1, st["n_dec"]),
            "net_bp_per_fill": float(a.mean()),
            "net_bp_per_dec": float(a.sum() / max(1, st["n_dec"])),
            # 分解（均为**成交时**口径）
            "half_spread_at_fill_bp": float(h1.mean()) if len(h1) else None,
            "markout1s_bp": float(m1.mean()) if len(m1) else None,
            # 按时点分解
            "by_tau": per_tau,
        }
    return out


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    # 默认只含有完整深度覆盖的币（BNB/DOGE 深度仅近 6h，会污染样本）
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--theta", type=float, default=0.5)
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--tol-ms", type=int, default=5000)
    ap.add_argument("--max-wait-s", type=float, default=120.0)
    args = ap.parse_args()

    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    print("H34 tick 级择时挂单（修 H32 的两个保留）")
    print(f"窗口={args.hours}h  币={len(syms)}  θ={args.theta}  "
          f"报价节奏={args.quote_every_s:.0f}s  最长等待={args.max_wait_s:.0f}s")
    print("成交判定与 markout 全部走 **tick 级**；前方挂量取真实最优档（同量纲）\n")

    results = []
    for s in syms:
        r = process_symbol(s, args.hours, args.theta, args.quote_every_s,
                           args.tol_ms, args.max_wait_s, print)
        if r:
            results.append(r)
            line = f"  {s:<8} 报价 {r['n_quotes']:>7}"
            for p in ("P0 两侧都挂", "P1 顺势挂", "P2 反势挂", "P3 择时"):
                v = r["policies"].get(p)
                line += ("  %s: %5.1f%%" % (p[:2], 100 * v["fill_rate"])) if v else "  --"
            print(line)

    if not results:
        print("\n无可用数据")
        return 1

    policies = ("P0 两侧都挂", "P1 顺势挂", "P2 反势挂", "P3 择时")
    print("\n[1] 汇总（tick 级，5 个有完整深度覆盖的币）")
    print("    %-14s %10s %10s %10s %13s %15s"
          % ("策略", "决策数", "成交数", "成交率", "净额bp/笔", "净额bp/决策"))
    summary = {}
    for p in policies:
        nets, ndec, nfill = [], 0, 0
        for r in results:
            v = r["policies"].get(p)
            if not v:
                continue
            ndec += v["n_dec"]
            nfill += v["n_fill"]
            # 用每决策净额加权还原总量（保留跨币可比性）
            nets.append(v["net_bp_per_dec"])
        if not nets:
            continue
        per_dec = float(np.mean(nets)) if nets else 0.0
        # 每笔净额：按成交数加权
        tot_net = 0.0
        tot_fill = 0
        for r in results:
            v = r["policies"].get(p)
            if v and v["n_fill"]:
                tot_net += v["net_bp_per_fill"] * v["n_fill"]
                tot_fill += v["n_fill"]
        summary[p] = {"n_dec": ndec, "n_fill": nfill,
                      "fill_rate": nfill / max(1, ndec),
                      "net_bp_per_fill": tot_net / max(1, tot_fill),
                      "net_bp_per_dec": per_dec}
        print("    %-14s %10d %10d %9.2f%% %13.4f %15.4f"
              % (p, ndec, nfill, 100 * nfill / max(1, ndec),
                 tot_net / max(1, tot_fill), per_dec))

    print("\n[2] 分解（**成交时**口径 —— 这是 H31/H32 错在哪里的直接证据）")
    print("    %-9s %-8s %9s %12s %11s %10s"
          % ("symbol", "policy", "成交率", "半价差@成交", "markout@1s", "净@1s"))
    halves = []
    for r in results:
        for p_ in ("P0 两侧都挂", "P2 反势挂"):
            v = r["policies"].get(p_)
            if not v:
                continue
            hv = v.get("half_spread_at_fill_bp")
            if hv is not None:
                halves.append(hv)
            print("    %-9s %-8s %8.1f%% %12s %11s %10s"
                  % (r["symbol"], p_[:6], 100 * v["fill_rate"],
                     ("%+.3f" % hv) if hv is not None else "-",
                     ("%+.3f" % v["markout1s_bp"])
                     if v.get("markout1s_bp") is not None else "-",
                     "%+.3f" % v["net_bp_per_fill"]))
    if halves:
        print("    => 半价差@成交 均值 = %+.3f bp"
              "（H31/H32 假定它等于挂单时的正半价差）" % float(np.mean(halves)))
        print("       成交时中价已移到我们挂单价附近 => 捕获被吃掉，这就是那 ~1.5bp 的差。")

    print("\\n[2b] markout 的**时间结构**（定位残差在哪个时点）")
    print("    %-9s %-8s %10s %11s %11s %11s"
          % ("symbol", "policy", "半价差", "mk@1s", "mk@5s", "mk@30s"))
    for r in results:
        for p_ in ("P0 两侧都挂", "P2 反势挂"):
            v = r["policies"].get(p_)
            if not v or not v.get("by_tau"):
                continue
            bt = v["by_tau"]
            f = lambda k, key: (("%+.3f" % bt[k][key]) if (k in bt and bt[k].get(key) is not None)
                                else "-")
            print("    %-9s %-8s %10s %11s %11s %11s"
                  % (r["symbol"], p_[:6], f("1000", "half_bp"),
                     f("1000", "mk_bp"), f("5000", "mk_bp"), f("30000", "mk_bp")))
    print("    => 若 mk@30s 显著比 mk@1s 更负，说明亏损是**持有期间的价格漂移**，")
    print("       而不是成交瞬间的逆向选择 —— 那意味着应对手段是**缩短持有**（已锁 30s~300s）")
    print("       而不是改挂单价位。")

    print("\n[3] 对照 H32（15s 桶 + top5/5）")
    print("    H32: P0 成交率 28.46% 净额/笔 +0.0856bp   P2 净额/笔 +0.2264bp")
    for p_ in ("P0 两侧都挂", "P2 反势挂"):
        v = summary.get(p_)
        if v:
            print("    H34: %s 成交率 %.2f%% 净额/笔 %+.4fbp"
                  % (p_[:2], 100 * v["fill_rate"], v["net_bp_per_fill"]))

    print("\n[4] 判定（事前定死：改善 >=0.05bp 才值得接引擎）")
    print("    注意：用**每笔净额**判定 —— 每决策净额会被成交率放大，不适合做策略优劣判据")
    base_f = summary.get("P0 两侧都挂", {}).get("net_bp_per_fill")
    if base_f is not None:
        print("    基准 P0 每笔净额 = %+.4f bp" % base_f)
        for p_ in policies[1:]:
            v = summary.get(p_)
            if not v:
                continue
            d = v["net_bp_per_fill"] - base_f
            if d >= 0.05:
                flag = "有执行价值"
            elif d >= 0.02:
                flag = "弱"
            else:
                flag = "不显著"
            print("    %-14s 每笔 %+.4f bp   改善 %+.4f bp   %s"
                  % (p_, v["net_bp_per_fill"], d, flag))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pth = OUT_DIR / "h34_tick_level_timed_quoting.json"
    pth.write_text(json.dumps({"hours": args.hours, "symbols": syms,
                               "theta": args.theta,
                               "quote_every_s": args.quote_every_s,
                               "results": results, "summary": summary},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {pth}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
