# -*- coding: utf-8 -*-
"""H423 P5 regime 突变桥：波动尖峰/流持续/价差跳变 → 触发该币条件化复核（h398 模式）。

设计对应《随市进化系统设计_20260927.md》§3.2/§5 事件驱动行：
"regime 突变（vol 尖峰/流持续/价差跳变）→ 启动该币条件化复核"。

判据（全部只在已触发币上跑，防洪泛）：
  1. vol 尖峰：当前 5min 已实现波动 ≥ 2.5× 过去 12h 中位（h220 口径）；
  2. 流持续：15s OFI 归一失衡同号 ≥0.3 连续 ≥3 桶（flow_streak 口径）；
  3. 价差跳变：最近 15min 中位半价差 ≥ 2× 过去 12h 基线。
任一触发 ⇒ 对该币跑近 6h 条件化复核（只读，产物注入 h375 清单排队）：

复核的已上线条件闸（当前线上真实生效者）：
  A. P1 回调闸 p1_trigger_bp=15：|r300|≥15bp × OFI 流向桶 × f60/f300 → fade edge；
  B. P2 VWAP 回归 vwap_revert_bp=2.0：|60s vwap 偏离|≥2bp × 流向桶 × f60/f120；
  C. 统一流定律基座 ofi_confirm=0.15：OFI 流向桶 × f60/f120 无条件边际。
裁决：闸的期望符号失守（A/B with 桶 ≤0、C with≤0 或 against≥0）⇒ 产出复核卡。

限流：状态文件按 (币, 闸, 日) 去重，全宇宙 ≤6 卡/日；只读扫描不改任何状态。

用法: python scripts/h423_regime_break_bridge.py [--hours 12] [--dry-run]
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
OUT = ROOT / "research_l1" / "out" / "h423_regime_cards.json"
STATE = ROOT / "research_l1" / "out" / "h423_state.json"
BACKLOG = ROOT.parent / "研究结论" / "h375_phase2_backlog_20260927.md"
MAX_CARDS_DAY = 6
VOL_RATIO = 3.5          # [h423a] 2.5× 太烫：全币每轮都触发（5min vol 天然摇摆）
VOL_FLOOR_BP = 3.0       #        且加绝对地板：安静币的 3.5× 仍是无意义涟漪
SPREAD_RATIO = 2.0
SPREAD_FLOOR_BP = 0.2
FLOW_STREAK_N = 5        # [h423a] 3 桶太烫 → 5 桶（75s 持续）
FLOW_T = 0.3             # 流向桶阈值（与 h358/h422 口径一致）
FLOW_STREAK_T = 0.5      # [h423a] 突变触发器要求更强的持续失衡
T_FLAG = 2.0             # [h423a] 复核卡须 |t|≥2 且符号失守——过滤弱证据
P1_TRIGGER_BP = 15.0
VWAP_DEV_BP = 2.0
RECHECK_HOURS = 6.0


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


def _t(xs):
    n = len(xs)
    if n < 20:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return None
    return (n, m, m / math.sqrt(var / n))


def _load_grid(cur, sym, t0, t1):
    """5s 桶末 (mid, half_spread_bp)——book_ticker 全量 + Python 降采样
    （12h 仅 ~10 万行/币，普通有序查询 + autocommit）。"""
    cur.execute("""
        SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
        WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
          AND bid_px>0 AND ask_px>bid_px
        ORDER BY event_ts_ms
    """, (sym + "USDT", t0, t1))
    grid = {}
    for ms, bid, ask in cur.fetchall():
        b = (int(ms) // 5000) * 5
        mid = (float(bid) + float(ask)) / 2.0
        hb = (float(ask) - float(bid)) / (2.0 * mid) * 1e4
        grid[b] = (mid, hb)
    return grid


def _load_ofi(cur, sym, t0, t1):
    cur.execute("""
        SELECT timestamp, COALESCE(SUM(taker_buy_notional),0), COALESCE(SUM(taker_sell_notional),0)
        FROM market_trades_aggregated
        WHERE exchange='asterdex' AND symbol=%s AND timestamp >= %s AND timestamp <= %s
        GROUP BY timestamp ORDER BY timestamp
    """, (sym, t0, t1))
    out = {}
    for ts_ms, bv, sv in cur.fetchall():
        tot = float(bv) + float(sv)
        if tot > 0:
            out[int(ts_ms) // 1000] = (float(bv) - float(sv)) / tot
    return out


def _regime_breaks(grid, ofi):
    """返回触发的突变类型集合。grid: t5s -> (mid, half_bp)。"""
    fired = set()
    if len(grid) < 1200:
        return fired
    ts = sorted(grid)
    # 5min 已实现波动序列（12 个 5s 步）
    rv = []
    for k in range(12, len(ts)):
        m0 = grid[ts[k - 12]][0]
        if m0 <= 0:
            continue
        rv.append((ts[k], abs(grid[ts[k]][0] - m0) / m0 * 1e4))
    if len(rv) >= 240:
        cut = ts[-1] - 3600       # [h423b] 基线剔除最近 60min：测"跳变"而非"水平"，
        base = sorted(v for t, v in rv if t < cut)      # 否则高波动持续数小时后基线
        if len(base) >= 60:                             # 跟着抬高，桥仍会天天全币触发
            base = base[len(base) // 2]
            recent = [v for t, v in rv if t >= ts[-1] - 900]
            if len(recent) >= 6:
                cur = sorted(recent)[len(recent) // 2]
                if base > 0 and cur >= VOL_RATIO * base and cur >= VOL_FLOOR_BP:
                    fired.add("vol_spike")
    # 价差跳变：末 15min 中位 vs 12h 基线
    hb = [grid[t][1] for t in ts]
    base_sp = sorted(hb[:-180])[len(hb[:-180]) // 2] if len(hb) > 360 else 0.0
    cur_sp = sorted(hb[-180:])[len(hb[-180:]) // 2]
    if base_sp > 0 and cur_sp >= SPREAD_RATIO * base_sp and cur_sp >= SPREAD_FLOOR_BP:
        fired.add("spread_jump")
    # 流持续：15s OFI 同号 ≥0.3 连续 ≥3 桶
    t1 = ts[-1]
    streak = 0
    best = 0
    last_sign = 0
    for t in range((t1 // 15) * 15 - 14 * 15, (t1 // 15) * 15 + 1, 15):
        v = ofi.get(t, 0.0)
        s = 1 if v >= FLOW_STREAK_T else (-1 if v <= -FLOW_STREAK_T else 0)
        if s == 0:
            streak = 0
            last_sign = 0
        elif s == last_sign:
            streak += 1
        else:
            streak = 1
            last_sign = s
        best = max(best, streak)
    if best >= FLOW_STREAK_N:
        fired.add("flow_streak")
    return fired


def _recheck(cur, sym, grid, ofi, t0, t1):
    """近 6h 条件化复核（h398 模式）：A P1 闸 / B P2 vwap 闸 / C 流律基座。"""
    cells = {}
    ts = sorted(grid)
    if len(ts) < 1200:
        return cells
    # C 流律基座（无条件）
    for i, t in enumerate(ts):
        if i < 24 or i + 24 >= len(ts):
            continue
        fo = ofi.get((t // 15) * 15, 0.0)
        flow = "with" if fo >= FLOW_T else ("against" if fo <= -FLOW_T else "neutral")
        m0 = grid[t][0]
        for steps, hname in ((12, "f60"), (24, "f120")):
            j = i + steps
            fwd = (grid[ts[j]][0] - m0) / m0 * 1e4
            cells.setdefault(("C", flow, hname), []).append(fwd)
    # A P1 回调闸（fade：正 r300 → 空）
    for i, t in enumerate(ts):
        if i < 60 or i + 60 >= len(ts):
            continue
        m0 = grid[t][0]
        m_prev = grid[ts[i - 60]][0]
        if m_prev <= 0:
            continue
        r300 = (m0 - m_prev) / m_prev * 1e4
        if abs(r300) < P1_TRIGGER_BP:
            continue
        d = -1.0 if r300 > 0 else 1.0
        fo = ofi.get((t // 15) * 15, 0.0) * d
        flow = "with" if fo >= FLOW_T else ("against" if fo <= -FLOW_T else "neutral")
        for steps, hname in ((12, "f60"), (60, "f300")):
            j = i + steps
            fwd = (grid[ts[j]][0] - m0) / m0 * 1e4 * d
            cells.setdefault(("A", flow, hname), []).append(fwd)
    # B P2 VWAP 回归（trades 60s VWAP 偏离 ≥2bp）
    cur.execute("""
        SELECT event_ts_ms/1000 AS t, price, qty FROM asterdex_trades
        WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
        ORDER BY event_ts_ms
    """, (sym + "USDT", t0, t1))
    tr = cur.fetchall()
    if len(tr) >= 500:
        import bisect
        ts_t = [r[0] for r in tr]
        pv = [float(r[1]) * float(r[2]) for r in tr]
        qv = [float(r[2]) for r in tr]
        for i, t in enumerate(ts):
            if i < 12 or i + 24 >= len(ts):
                continue
            j = bisect.bisect_right(ts_t, t)
            k = bisect.bisect_right(ts_t, t - 60)
            if j <= k:
                continue
            sqv = sum(qv[k:j])
            if sqv <= 0:
                continue
            vwap = sum(pv[k:j]) / sqv
            m0 = grid[t][0]
            dev = (m0 - vwap) / m0 * 1e4
            if abs(dev) < VWAP_DEV_BP:
                continue
            d = -1.0 if dev > 0 else 1.0
            fo = ofi.get((t // 15) * 15, 0.0) * d
            flow = "with" if fo >= FLOW_T else ("against" if fo <= -FLOW_T else "neutral")
            for steps, hname in ((12, "f60"), (24, "f120")):
                j2 = i + steps
                fwd = (grid[ts[j2]][0] - m0) / m0 * 1e4 * d
                cells.setdefault(("B", flow, hname), []).append(fwd)
    out = {}
    for (gate, flow, hname), xs in cells.items():
        st = _t(xs)
        if st:
            out[f"{gate}_{flow}_{hname}"] = {"n": st[0], "mean_bp": round(st[1], 3),
                                             "t": round(st[2], 2)}
    return out


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            m = cur.fetchone()[0]
            syms = [str(s) for s in (m.get("symbols") or []) if str(s)]

    t1 = int(dt.datetime.now(dt.timezone.utc).timestamp()) * 1000
    t0 = t1 - int(a.hours * 3600 * 1000)
    r0 = t1 - int(RECHECK_HOURS * 3600 * 1000)

    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    today = dt.date.today().isoformat()
    day_cards = sum(1 for v in state.values() if v.get("day") == today)
    cards = []

    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for sym in syms:
                try:
                    grid = _load_grid(cur, sym, t0, t1)
                    ofi = _load_ofi(cur, sym, t0, t1)
                except Exception as e:
                    print(f"  {sym}: 数据加载失败（跳过）{e}", flush=True)
                    continue
                fired = _regime_breaks(grid, ofi)
                if not fired:
                    continue
                print(f"  {sym}: regime 突变 {sorted(fired)} → 近 {RECHECK_HOURS:.0f}h 条件化复核", flush=True)
                rc = _recheck(cur, sym, grid, ofi, r0, t1)
                for key, v in sorted(rc.items()):
                    gate = key[0]
                    flag = None
                    if gate == "C":
                        if key.startswith("C_with") and v["mean_bp"] <= 0:
                            flag = "流律 with 桶失正"
                        elif key.startswith("C_against") and v["mean_bp"] >= 0:
                            flag = "流律 against 桶失负"
                    elif gate in ("A", "B"):
                        if key.endswith("with_f60") and v["mean_bp"] <= 0:
                            flag = "with_f60 闸边际失正"
                        elif key.endswith("against_f60") and v["mean_bp"] >= 0:
                            flag = "against_f60 闸边际失负"
                    if flag and abs(v["t"]) >= T_FLAG:
                        dk = f"{sym}|{key}|{today}"
                        if dk in state or day_cards >= MAX_CARDS_DAY:
                            continue
                        card = {"symbol": sym, "gate": key, "day": today,
                                "breaks": sorted(fired),
                                "n": v["n"], "mean_bp": v["mean_bp"], "t": v["t"],
                                "flag": flag}
                        cards.append(card)
                        state[dk] = {"day": today, "key": key}
                        day_cards += 1

    res = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "window_hours": a.hours, "recheck_hours": RECHECK_HOURS,
           "symbols": syms, "cards": cards}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    if not a.dry_run:          # [h423b] dry-run 不写状态/不占日配额（纯验证）
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"复核卡 {len(cards)} 张（日配额余 {MAX_CARDS_DAY - day_cards}）")
    for c in cards:
        print(f"  ⚠ {c['symbol']} {c['gate']} {c['flag']}：n={c['n']} "
              f"mean={c['mean_bp']:+.3f}bp t={c['t']:+.1f}（{','.join(c['breaks'])}）")

    if cards and not a.dry_run:
        sec = [f"\n## [h423 regime 突变复核 · {dt.datetime.now():%Y-%m-%d %H:%M}]（P5 事件驱动）\n"]
        for c in cards:
            sec.append(f"- {c['symbol']} {c['gate']}：{c['flag']}（n={c['n']} "
                       f"mean={c['mean_bp']:+.3f}bp t={c['t']:+.1f}；突变={','.join(c['breaks'])}）")
        if BACKLOG.exists():
            with open(BACKLOG, "a", encoding="utf-8") as f:
                f.write("\n".join(sec) + "\n")
            print(f"已写入 {BACKLOG.name}")
    elif cards:
        print("[dry-run] 未写入 BACKLOG")
    print(f"已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
