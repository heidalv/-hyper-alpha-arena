"""H39：真实往返净额 —— 用可信成交模型测「做一次完整往返」能赚多少。

## 为什么换成这条路（H38 的教训）

H38 想扫"报价位置"，但它需要**合成挂单价**（`bid × (1 − w/1e4)`）。
合成价与真实订单簿不在同一个"价位集合"里 ⇒ 无法精确判断"是否轮到我们成交"，
导致守卫怎么写都错：松了（w=+0.5 拿到 83% 成交率，物理上不可能），
紧了（w>0 几乎零成交）。**结论：合成报价的路走不通。**

## 换成直接测真实往返

不做合成的挂单价，只做**真实存在过的最优价**：

    ① 在某个快照的最优买价 `bid` 上挂买单（LA = 该档真实挂量，我们排最后）
    ② 成交条件：对手方主动卖量 ≥ LA **且** 该价位的量被吃穿（用逐笔判定）
    ③ 成交后**立即在同一时刻的最优卖价 `ask` 上挂卖单**（平仓腿），做同样的判定
    ④ 两条腿都成交 ⇒ 一个完整往返，净额 = (卖价 − 买价)/买价 × 1e4
       **不减去任何 markout** —— 因为往返已经把持仓风险兑现了
    ⑤ 加**超时**：平仓腿超过 `max_hold_s` 未成交 ⇒ 放弃该往返（不计入），
       并单独统计"卡住的往返"有多少 —— 这是库存风险的度量

## 与之前所有测量的区别（关键）

H31–H34 测的是**单腿**净额（捕获 + markout），需要 markout 口径、容易出错。
本脚本测的是**往返**净额 —— **不需要 markout**，只需要两笔真实成交价。
⇒ **口径错误的空间小得多。**

## 判据（事先定死）

  · 往返净额中位数 > 0 且 均值 > 0 ⇒ 策略正收益，可上线
  · 均值 > 0 但中位 < 0 ⇒ 靠少数大赢利，不稳
  · 卡住率（超时未平仓）> 30% ⇒ 库存风险不可接受

用法：
    .venv\\Scripts\\python.exe scripts\\h39_real_roundtrip_pnl.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h39_real_roundtrip_pnl.json"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, quote_every_s, entry_wait_s, exit_wait_s, maker_fee_bp, log):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = s if s.endswith("USDT") else f"{s}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours*3600_000)}"

    cur.execute(
        "SELECT event_ts_ms, (bids->0->>0)::float bp, (bids->0->>1)::float bq,"
        "       (asks->0->>0)::float ap, (asks->0->>1)::float aq"
        f"  FROM asterdex_depth_snapshots WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    dep = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    cn.close()
    if len(dep) < 2000 or len(tr) < 1000:
        log(f"  {s:<8} 数据不足 → 跳过")
        return None

    dts = np.array([int(x["event_ts_ms"]) for x in dep], dtype=np.int64)
    dbp = np.array([float(x["bp"] or 0) for x in dep])
    dbq = np.array([float(x["bq"] or 0) for x in dep])
    dap = np.array([float(x["ap"] or 0) for x in dep])
    daq = np.array([float(x["aq"] or 0) for x in dep])
    tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in tr])
    tq = np.array([float(x["q"]) for x in tr])
    tsell = np.array([bool(x["ibm"]) for x in tr])       # 主动卖

    # 想挂买单：找"价格在 px 被主动卖吃穿"的第一个时刻
    def find_fill(px, la, t_from, must_sell, wait_ms):
        """在 [t_from, t_from+wait_ms) 内找第一个满足累计量 ≥ la 的成交。

        `must_sell=True`（买单腿）：只累计**主动卖**且价≈px 的成交。
        `must_sell=False`（卖单腿）：只累计**主动买**且价≈px 的成交。
        返回成交时刻或 -1。
        """
        j0 = int(np.searchsorted(tts, t_from, "left"))
        j1 = int(np.searchsorted(tts, t_from + wait_ms, "left"))
        cum = 0.0
        for jj in range(j0, min(j1, len(tts))):
            if tsell[jj] != must_sell:
                continue
            if abs(tpx[jj] - px) / px * 1e4 > 1.0:
                continue
            cum += float(tq[jj])
            if cum >= la:
                return jj
        return -1

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    qidx = np.arange(0, len(dts), step, dtype=np.int64)
    entry_wait_ms = int(entry_wait_s * 1000)
    exit_wait_ms = int(exit_wait_s * 1000)

    rts = []
    stuck = 0
    no_entry = 0
    for i in qidx:
        bid, la_b = float(dbp[i]), float(dbq[i])
        if bid <= 0 or la_b <= 0:
            continue
        t0 = int(dts[i])
        # ① 买单腿成交
        je = find_fill(bid, la_b, t0, True, entry_wait_ms)
        if je < 0:
            no_entry += 1
            continue
        t_entry = int(tts[je])
        # ② 平仓腿：挂在**成交时刻**的最优卖价上
        di = int(np.searchsorted(dts, t_entry, "right")) - 1
        if di < 0 or float(dap[di]) <= 0 or float(daq[di]) <= 0:
            continue
        ask, la_a = float(dap[di]), float(daq[di])
        if ask <= bid:
            continue
        jx = find_fill(ask, la_a, t_entry, False, exit_wait_ms)
        if jx < 0:
            stuck += 1
            continue
        t_exit = int(tts[jx])
        gross = (ask - bid) / bid * 1e4
        net = gross - 2 * maker_fee_bp
        rts.append({"gross_bp": gross, "net_bp": net,
                    "hold_ms": t_exit - t_entry,
                    "entry_px": bid, "exit_px": ask})

    if not rts:
        log(f"  {s:<8} 无完整往返（未成交 {no_entry}，卡住 {stuck}）")
        return None

    g = np.array([r["gross_bp"] for r in rts])
    n = np.array([r["net_bp"] for r in rts])
    h = np.array([r["hold_ms"] for r in rts])
    return {
        "symbol": s, "n_roundtrips": len(rts), "n_no_entry": no_entry, "n_stuck": stuck,
        "stuck_rate": stuck / max(1, stuck + len(rts)),
        "gross_bp_mean": float(g.mean()), "gross_bp_median": float(np.median(g)),
        "net_bp_mean": float(n.mean()), "net_bp_median": float(np.median(n)),
        "hold_ms_median": float(np.median(h)),
        "hold_ms_p75": float(np.percentile(h, 75)),
        "win_rate": float((n > 0).mean()),
        "maker_fee_bp": maker_fee_bp,
    }


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--entry-wait-s", type=float, default=120.0)
    ap.add_argument("--exit-wait-s", type=float, default=300.0)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0,
                    help="Aster 零售 0.0；注册做市商档用 -0.5")
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H39 真实往返净额（不需要 markout ⇒ 口径错误空间小）")
    print(f"窗口={args.hours}h  币={len(syms)}  报价节奏={args.quote_every_s:.0f}s")
    print(f"入场等待={args.entry_wait_s:.0f}s  出场等待={args.exit_wait_s:.0f}s  "
          f"maker 费={args.maker_fee_bp:+.2f}bp/腿\n")

    res = []
    for s in syms:
        r = run_symbol(s, args.hours, args.quote_every_s, args.entry_wait_s,
                       args.exit_wait_s, args.maker_fee_bp, print)
        if r:
            res.append(r)
            print("  %-8s 往返 %4d  均价差 %+7.3fbp  净 %+7.3fbp  "
                  "持仓中位 %6.0fms  胜率 %4.1f%%  卡住 %4.1f%%"
                  % (r["symbol"], r["n_roundtrips"], r["gross_bp_mean"],
                     r["net_bp_mean"], r["hold_ms_median"],
                     100 * r["win_rate"], 100 * r["stuck_rate"]))
    if not res:
        print("\n无完整往返")
        return 1

    N = sum(r["n_roundtrips"] for r in res)
    net_mean = sum(r["net_bp_mean"] * r["n_roundtrips"] for r in res) / N
    net_med = float(np.median([r["net_bp_median"] for r in res]))
    gross_mean = sum(r["gross_bp_mean"] * r["n_roundtrips"] for r in res) / N
    hr = sum(r["hold_ms_median"] * r["n_roundtrips"] for r in res) / N
    wr = sum(r["win_rate"] * r["n_roundtrips"] for r in res) / N
    sr = sum(r["stuck_rate"] * (r["n_roundtrips"] + r["n_stuck"]) for r in res) / \
        max(1, sum(r["n_roundtrips"] + r["n_stuck"] for r in res))

    print("\n[汇总] 跨币按往返数加权")
    print("    完整往返数        %d" % N)
    print("    毛价差（往返）    %+.4f bp" % gross_mean)
    print("    **净额（往返）    %+.4f bp**" % net_mean)
    print("    净额中位（各币）  %+.4f bp" % net_med)
    print("    持仓中位          %.0f ms" % hr)
    print("    胜率              %.1f%%" % (100 * wr))
    print("    卡住率（超时未平）%.1f%%" % (100 * sr))
    print("    ⇒ 每 $30 腿 ≈ %+.6f 美元/往返" % (net_mean / 1e4 * 30))

    print("\n[判据]")
    ok = True
    if net_mean > 0 and net_med > 0:
        print("    ⇒ ✓ 净额均值与中位都为正 ⇒ **策略正收益**")
    elif net_mean > 0:
        print("    ⇒ ~ 均值为正但中位为负 ⇒ 靠少数大赢利，不稳")
        ok = False
    else:
        print("    ⇒ ✗ 净额为负 ⇒ 该配置不成立")
        ok = False
    if sr > 0.30:
        print("    ⇒ ✗ 卡住率 %.1f%% > 30%% ⇒ 库存风险不可接受" % (100 * sr))
        ok = False
    elif sr > 0:
        print("    ⇒ 卡住率 %.1f%%（可接受范围）" % (100 * sr))
    print("\n    ⚠️ 口径说明：")
    print("    · 平仓腿挂在**入场成交时刻**的最优卖价上，等待 %d 秒；" % args.exit_wait_s)
    print("      超时未成交的往返**不计入**净额，单独记入卡住率。")
    print("    · 不含手续费时 maker_fee_bp=0；注册做市商档用 --maker-fee-bp -0.5。")
    print("    · **不需要 markout** ⇒ 与 H31–H34 的单腿口径相比，错误空间小得多。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms,
        "entry_wait_s": args.entry_wait_s, "exit_wait_s": args.exit_wait_s,
        "maker_fee_bp": args.maker_fee_bp, "per_symbol": res,
        "summary": {"n_roundtrips": N, "gross_bp_mean": gross_mean,
                    "net_bp_mean": net_mean, "net_bp_median_sym": net_med,
                    "hold_ms_median": hr, "win_rate": wr, "stuck_rate": sr,
                    "usd_per_roundtrip_30leg": net_mean / 1e4 * 30},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0 if ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
