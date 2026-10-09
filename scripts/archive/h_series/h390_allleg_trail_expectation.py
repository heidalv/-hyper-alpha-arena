# -*- coding: utf-8 -*-
"""H390 #17 试跑期望预演：尾随锁利规则应用到 #2 时代**全部**已平仓腿。

h388 只重演了 16 笔止损腿（结论 +999.6bp）。本脚本把同一规则应用到窗口内
每一笔出场腿，给出试跑的真实期望面（含代价面）：

  对每笔出场腿（lane_ledger 行，exit_path<>'' 或 maker 减仓行）：
    A（基线）= 账本已实现 net_bp；
    B（尾随）= 沿 1s 中价路径从入场腿 (fill_px, side) 重演：
      初始止损 −40；MFE≥+20 ⇒ 线 = −5+10×⌊(MFE−20)/10⌋（与生产同式，含
      round(,6) 浮点护栏）；ret ≤ 线 ⇒ 该秒触发。
      触发时刻早于该行实际出场 ts ⇒ B = 触发 bp − 4bp taker 费；
      否则（尾随不影响这条腿）B = A。
    机会成本 = 触发后继续走到实际出场 ts 的方向最大有利漂移（仅受影响腿）。

已知乐观假设（h389 协议 §6，试跑将仲裁）：
  1s 中价即时成交、无滑点/穿透；1s 路径 vs 生产 15s tick 更早触发。

用法: python scripts/h390_allleg_trail_expectation.py
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h390_allleg_trail_expectation.json"

WINDOW_START = "2026-09-27 13:00:00+08"   # #2 宇宙试跑部署时刻
WINDOW_START_DT = dt.datetime.fromisoformat(WINDOW_START.replace("+08", "+08:00"))
ENTRY_LOOKBACK_DAYS = 10.0                # 覆盖孤儿仓（ADA 4.8d / ARB 7d）
TAKER_FEE_BP = 4.0
TP, STOP0, BE_AT = 30.0, 40.0, 20.0
MAX_WALK_SEC = 9 * 3600                   # 数据/重演上界（孤儿仓截断，未触发即 A）
GRACE_SEC = 30.0                          # 生产 stop_maker_grace_sec（触发→maker 宽限→taker）
OPP_WINDOW_SEC = 1800.0                   # 机会成本观察窗（与判定脚本 h389 一致）


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


def trail_line(mfe_bp: float) -> float:
    """与生产 runner 完全同式（含浮点护栏）。"""
    mfe_r = round(float(mfe_bp), 6)
    if mfe_r < BE_AT:
        return -STOP0
    return -5.0 + 10.0 * ((mfe_r - BE_AT) // 10.0)


def main() -> int:
    import psycopg

    # ── 1. 出场腿 + 入场腿 ──
    # position_id 是**逐币计数器**，跨天跨方向复用（实测 mm:ENA:3 的 rn=1 是 09-21 的
    # 入场，而其 09-27 的出场属于另一条腿）⇒ rn=1 配对法系统性错位（h388 只在
    # NEAR/SUI 这类新币上可信）。正确配对：出场行 = 其前**最近一条反向成交行**
    # （qty 精确吻合：stop sell 1066.48 ↔ 4.5min 前 buy 1066.48；部分平仓腿则取
    # 最近一次加仓 —— 与生产「avg_mid 变化即重置 MFE 锚」的语义一致）。
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, position_id, ts, net_bp, meta_json
                FROM lane_ledger
                WHERE lane_id='mm_asterdex'
                  AND ts > %s::timestamptz - interval '3 hours'
                ORDER BY ts
            """, (WINDOW_START,))
            all_rows = cur.fetchall()

    legs = []
    for sym, pid, ts, net_bp, meta in all_rows:
        side = (meta or {}).get("side")
        exit_path = (meta or {}).get("exit_path") or ""
        if not exit_path or side not in ("buy", "sell"):
            continue
        opp = "sell" if side == "buy" else "buy"
        entry = None
        for sym2, pid2, ts2, net_bp2, meta2 in reversed(all_rows):
            if ts2 >= ts or sym2 != sym:
                continue
            if (meta2 or {}).get("side") == opp and not ((meta2 or {}).get("exit_path") or ""):
                entry = (sym2, pid2, ts2, meta2)
                break
        if not entry or entry[2] < WINDOW_START_DT:
            # 只保留窗口内开仓的"新鲜腿"：跨天/跨窗口的多加仓腿无法廉价重建
            # 真实 avg_mid 轨迹（引擎每笔加仓都会重置均价锚），不重演。
            continue
        # 参考价 = 入场行 mark mid（≈引擎 avg_mid 锚），不是 fill_px（含点差偏移）
        entry_mid = float((entry[3].get("mid_px") or entry[3].get("fill_px") or 0.0) or 0.0)
        legs.append({
            "symbol": sym, "side": (entry[3] or {}).get("side"),   # 持仓方向 = 入场腿方向
            "entry_px": entry_mid,
            "entry_ts": entry[2],
            "exit_ts": ts, "net_bp": net_bp, "exit_path": exit_path,
        })

    def _leg_key(l):
        return (l["symbol"], l["exit_ts"], l["net_bp"])
    legs = [dict(t) for t in {tuple(sorted(l.items())): l for l in legs}.values()]
    n_total = len(legs)
    print(f"窗口内强制出场腿共 {n_total} 笔（反向成交配对：出场 ↔ 最近反向入场/加仓）",
          flush=True)

    # ── 2. 各币 1s 中价路径 + 15s 生产网格 ──
    # 生产 worker 每 ~15s tick 一次、用新鲜 1s 盘口中价判定（1s wick 对引擎可见），
    # 重演走 15s 网格（≈引擎采样节奏）；机会成本走 1s（与判定脚本 min/max 口径一致）。
    syms = sorted({l["symbol"] for l in legs})
    paths = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
        with c.cursor() as cur:
            for sym in syms:
                t0 = min(l["entry_ts"].timestamp() for l in legs if l["symbol"] == sym) - 120
                t1 = max(l["exit_ts"].timestamp() for l in legs if l["symbol"] == sym) + 60
                if t1 - t0 > MAX_WALK_SEC:
                    t1 = t0 + MAX_WALK_SEC
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
                ts_1s = [int(r[0]) for r in recs]
                mid_1s = [(float(r[1]) + float(r[2])) / 2.0 for r in recs]
                grid_ts, grid_mid = [], []
                _last_bucket = None
                for t, m in zip(ts_1s, mid_1s):
                    b = (t // 15) * 15
                    if b != _last_bucket:
                        grid_ts.append(b)
                        grid_mid.append(m)
                        _last_bucket = b
                    else:
                        grid_mid[-1] = m
                paths[sym] = (ts_1s, mid_1s, grid_ts, grid_mid)
    print(f"路径：{len(paths)} 币，1s 总秒数 {sum(len(p[0]) for p in paths.values())}，"
          f"15s 网格总点数 {sum(len(p[2]) for p in paths.values())}", flush=True)

    # ── 3. 重演（含生产 stop_maker_grace 宽限语义）──
    def simulate(ts_g, mid_g, ts_1s, mid_1s, entry_px, entry_ts, exit_ts, s):
        """返回 (b_bp, b_why, b_opp)。未在真实出场前触发 ⇒ (A, 'unchanged', 0)。"""
        i0 = bisect.bisect_left(ts_g, int(entry_ts.timestamp()))
        i_end = bisect.bisect_left(ts_g, int(exit_ts.timestamp()))
        mfe = 0.0
        i = i0
        while i < min(len(ts_g), i_end):
            ret = (mid_g[i] - entry_px) / entry_px * 1e4 * s
            if ret > mfe:
                mfe = ret
            if ret <= trail_line(mfe):
                # 生产语义：触发 → stop_since=now → 30s maker 宽限 → 到期仍 ≤ 线才 taker
                j = bisect.bisect_left(ts_g, ts_g[i] + int(GRACE_SEC))
                if j >= min(len(ts_g), i_end):
                    break                      # 宽限到期已过真实出场/数据端 ⇒ 不影响
                # 宽限期内 MFE 继续更新，到期线 = trail_line(MFE_到期)（生产逐 tick 重算）
                for k in range(i + 1, j + 1):
                    rk = (mid_g[k] - entry_px) / entry_px * 1e4 * s
                    if rk > mfe:
                        mfe = rk
                ret_j = (mid_g[j] - entry_px) / entry_px * 1e4 * s
                if ret_j <= trail_line(mfe):
                    # 机会成本（1s 口径，与判定脚本 min/max 一致）：
                    # 宽限到期后 30min 内、且不晚于真实出场的方向最大有利漂移
                    j1 = bisect.bisect_left(ts_1s, ts_g[j])
                    e1 = bisect.bisect_left(ts_1s, int(exit_ts.timestamp()))
                    k_end = min(len(ts_1s), e1,
                                bisect.bisect_left(ts_1s, ts_g[j] + OPP_WINDOW_SEC))
                    best = ret_j
                    for k in range(j1, k_end):
                        r2 = (mid_1s[k] - entry_px) / entry_px * 1e4 * s
                        if r2 > best:
                            best = r2
                    return ret_j - TAKER_FEE_BP, "trail", best - ret_j
                i = j + 1                              # 宽限内回摆 ⇒ 计时归零，继续走
                continue
            i += 1
        return None, "unchanged", 0.0

    rows = []
    tot_a = tot_b15 = tot_b1 = 0.0
    n_aff15 = n_aff1 = 0
    opps = []
    per_sym = {}
    for leg in legs:
        sym = leg["symbol"]
        side = leg["side"]
        entry_px = leg["entry_px"]
        entry_ts = leg["entry_ts"]
        exit_ts = leg["exit_ts"]
        net_bp = leg["net_bp"]
        exit_path = leg["exit_path"]
        ts_1s, mid_1s, ts_g, mid_g = paths.get(sym, ([], [], [], []))
        a_bp = float(net_bp or 0.0)
        s = 1.0 if side == "buy" else -1.0
        # 15s 网格（≈引擎 tick 节奏）为主口径；1s 网格为灵敏度上界
        r15, w15, o15 = (None, "unchanged", 0.0)
        r1, w1, o1 = (None, "unchanged", 0.0)
        if entry_px and ts_g:
            r15, w15, o15 = simulate(ts_g, mid_g, ts_1s, mid_1s,
                                     entry_px, entry_ts, exit_ts, s)
            r1, w1, o1 = simulate(ts_1s, mid_1s, ts_1s, mid_1s,
                                  entry_px, entry_ts, exit_ts, s)
        b15 = a_bp if r15 is None else r15
        b1 = a_bp if r1 is None else r1
        if w15 == "trail":
            n_aff15 += 1
            opps.append(o15)
        if w1 == "trail":
            n_aff1 += 1
        tot_a += a_bp
        tot_b15 += b15
        tot_b1 += b1
        ps = per_sym.setdefault(sym, {"A": 0.0, "B15": 0.0, "B1": 0.0,
                                      "n": 0, "aff15": 0, "aff1": 0})
        ps["A"] += a_bp
        ps["B15"] += b15
        ps["B1"] += b1
        ps["n"] += 1
        if w15 == "trail":
            ps["aff15"] += 1
        if w1 == "trail":
            ps["aff1"] += 1
        rows.append({"sym": sym, "side": side, "entry": str(entry_ts)[11:19],
                     "entry_px": round(entry_px, 6),
                     "exit_path": (exit_path or "maker")[:18],
                     "A_bp": round(a_bp, 1),
                     "B15_bp": round(b15, 1), "B15_why": w15,
                     "B1_bp": round(b1, 1), "B1_why": w1,
                     "opp_bp": round(o15, 1)})

    print(f"\n{'币':<6} {'腿数':>5} {'受影响':>6} {'A合计bp':>9} {'B15合计':>9} {'Δ15':>8} {'B1合计':>9} {'Δ1':>8}")
    for sym, ps in sorted(per_sym.items(), key=lambda kv: -(kv[1]["B15"] - kv[1]["A"])):
        print(f"{sym:<6} {ps['n']:>5} {ps['aff15']:>6} {ps['A']:>+9.1f} {ps['B15']:>+9.1f} "
              f"{ps['B15'] - ps['A']:>+8.1f} {ps['B1']:>+9.1f} {ps['B1'] - ps['A']:>+8.1f}")
    n_opp_gt0 = sum(1 for o in opps if o > 5)
    print(f"\n全部腿：A（已实现）= {tot_a:+.1f}bp")
    print(f"B(15s 网格) = {tot_b15:+.1f}bp ⇒ 期望 Δ = {tot_b15 - tot_a:+.1f}bp"
          f"（{n_total} 腿，受影响 {n_aff15} 腿）")
    print(f"B(1s 网格)  = {tot_b1:+.1f}bp ⇒ 期望 Δ = {tot_b1 - tot_a:+.1f}bp"
          f"（受影响 {n_aff1} 腿，灵敏度上界）")
    print(f"受影响腿机会成本(15s)：合计 {sum(opps):+.1f}bp，"
          f"中位 {sorted(opps)[len(opps)//2] if opps else 0:+.1f}bp，>+5bp 的 {n_opp_gt0} 腿")
    print(f"（B 已扣 4bp taker 费；即时成交，未建模滑点/穿透；"
          f"多加仓腿的均价重锚未建模 —— 试跑将仲裁）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "window_start": WINDOW_START, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "n_total": n_total, "n_affected_15s": n_aff15, "n_affected_1s": n_aff1,
        "tot_A_bp": round(tot_a, 1),
        "tot_B15_bp": round(tot_b15, 1), "delta_15s_bp": round(tot_b15 - tot_a, 1),
        "tot_B1_bp": round(tot_b1, 1), "delta_1s_bp": round(tot_b1 - tot_a, 1),
        "opp_sum_bp": round(sum(opps), 1),
        "opp_median_bp": round(sorted(opps)[len(opps) // 2], 1) if opps else 0.0,
        "opp_gt5_count": n_opp_gt0,
        "per_symbol": {k: {kk: round(vv, 1) for kk, vv in v.items()}
                       for k, v in per_sym.items()},
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
