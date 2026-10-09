# -*- coding: utf-8 -*-
"""H420 P1 每日增量发现扫描器：赚钱点主动发现流水线 v1。

设计对应《随市进化系统设计_20260927.md》§3.1（每日增量扫描）。

口径（与既有研究法逐字对齐）：
  · 网格：asterdex_book_ticker 全量 → 5s 桶末 mid（h361 口径）；[h420e] 价格源
    统一 book_ticker（~9 天留存）——深度表仅 65h，长窗会掏空前半窗；
    微价未纳入检测逻辑，暂不加载；
  · OFI：market_trades_aggregated 15s 桶归一化失衡，按 floor(ts/15) 贴到 5s 网格；
  · 形态：P1（|r300|≥15，回调 fade）、P3（|r75|≥3，尖峰 fade）、
    P4（120s 窗双触极值贴边）、P5（5 期波动<30分位 且 |r15|≥2）；
  · 时域：f30/f60/f120/f300（5s 网格 6/12/24/60 步）；
  · 流向桶：with(≥0.3)/neutral/against(≤−0.3)（统一流定律先验）；
  · OOS：split-half（前 12h vs 后 12h），双半窗 |t|≥2 且同号才晋级；
  · 先验罚则：against 桶为正的候选标记 flow_law_violation，双半窗复现才列卡。

产出：research_l1/out/h420_discovery_cards.json + 追加到 h375 清单
（"h420 自动发现候选"小节，日期戳）。只读扫描，不改任何状态。

用法: python scripts/h420_discovery_scan.py [--hours 24] [--dry-run]
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
OUT = ROOT / "research_l1" / "out" / "h420_discovery_cards.json"
BACKLOG = ROOT.parent / "研究结论" / "h375_phase2_backlog_20260927.md"

HORIZONS = [(6, "f30"), (12, "f60"), (24, "f120"), (60, "f300")]
MIN_EVENTS_CELL = 60          # 单格最小事件数
T_HALF = 2.0                 # 半窗显著门槛
FLOW_T = 0.3                 # 流向桶边界


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


def _t_stat(xs) -> tuple | None:
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return None
    t = m / math.sqrt(var / n)
    return (n, m, t)


def _load_coin(cur_market, sym: str, t0: int, t1: int):
    """5s 网格：mid / ofi / mpskew（普通有序查询 + Python 降采样）。

    [h420e] 价格源统一 book_ticker 全量（~9 天留存；深度表仅 65h 会毁掉周扫的
    前半窗；7.5M 行/币/168h 的 fetch 由 autocommit 连接完成，LeakGuard 不再误杀）。
    微价字段当前未被检测逻辑消费——不再加载（需要时再接，避免死重）。
    """
    cur_market.execute("""
        SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
        WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
          AND bid_px>0 AND ask_px>bid_px
        ORDER BY event_ts_ms
    """, (sym + "USDT", t0, t1))
    grid = []
    last_b = None
    for ms, bid, ask in cur_market.fetchall():
        b = (int(ms) // 5000) * 5
        if b != last_b:
            grid.append((b, (float(bid) + float(ask)) / 2.0))
            last_b = b
        else:
            grid[-1] = (b, (float(bid) + float(ask)) / 2.0)
    ts = [t for t, _ in grid]
    mids = [m for _, m in grid]

    cur_market.execute("""
        SELECT timestamp, COALESCE(SUM(taker_buy_notional),0), COALESCE(SUM(taker_sell_notional),0)
        FROM market_trades_aggregated
        WHERE exchange='asterdex' AND symbol=%s AND timestamp >= %s AND timestamp <= %s
        GROUP BY timestamp ORDER BY timestamp
    """, (sym, t0, t1))
    ofi_map = {}
    for ts_ms, bv, sv in cur_market.fetchall():
        tot = float(bv) + float(sv)
        if tot > 0:
            ofi_map[int(ts_ms) // 1000] = (float(bv) - float(sv)) / tot   # ms→s 对齐网格

    n = len(ts)
    ofis = [ofi_map.get((t // 15) * 15, 0.0) for t in ts]   # t 为秒，15s 桶键
    return ts, mids, ofis


def _detect(ts, mids, i):
    """返回 [(pattern, direction)]，direction=入场方向（+1 多 / −1 空）。"""
    out = []
    if i >= 60 and i + 60 < len(mids):
        r300 = (mids[i] - mids[i - 60]) / mids[i - 60] * 1e4
        if abs(r300) >= 15.0:
            out.append(("P1", -1.0 if r300 > 0 else 1.0))       # 回调 fade
        r75 = (mids[i] - mids[i - 15]) / mids[i - 15] * 1e4
        if abs(r75) >= 3.0:
            out.append(("P3", -1.0 if r75 > 0 else 1.0))        # 尖峰 fade
    if i >= 24:
        seg = mids[i - 24:i + 1]
        hi, lo = max(seg), min(seg)
        near_hi = sum(1 for m in seg if m >= hi * (1 - 5e-6))
        near_lo = sum(1 for m in seg if m <= lo * (1 + 5e-6))
        if near_hi >= 2 and mids[i] >= hi * (1 - 5e-6):
            out.append(("P4", 1.0))
        elif near_lo >= 2 and mids[i] <= lo * (1 + 5e-6):
            out.append(("P4", -1.0))
    if i >= 4:
        segv = mids[i - 4:i + 1]
        v_now = sum(abs((segv[t + 1] - segv[t]) / segv[t]) * 1e4
                    for t in range(len(segv) - 1) if segv[t] > 0)
        vs = [sum(abs((mids[k + 1] - mids[k]) / mids[k]) * 1e4
                  for k in range(j, j + 4) if mids[k] > 0)
              for j in range(max(0, i - 40), i - 3)]
        vs = [v for v in vs if v > 0]
        if v_now > 0 and vs:
            q30 = sorted(vs)[int(len(vs) * 0.3)]
            r15 = (mids[i] - mids[i - 1]) / mids[i - 1] * 1e4
            if v_now < q30 and abs(r15) >= 2.0:
                out.append(("P5", 1.0 if r15 > 0 else -1.0))
    return out


def main() -> int:
    # [h420f] Windows 控制台默认 GBK 打不出 ✓/⚠——强制 UTF-8 输出
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--dry-run", action="store_true", help="不写 h375 清单（仍写 JSON）")
    a = ap.parse_args()

    import psycopg

    # 宇宙
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            m = cur.fetchone()[0]
            syms = [str(s) for s in (m.get("symbols") or []) if str(s)]

    t1 = int(dt.datetime.now(dt.timezone.utc).timestamp()) * 1000
    t0 = t1 - int(a.hours * 3600 * 1000)
    half_s = t0 // 1000 + (t1 - t0) // 2000     # 秒口径中点（ts 网格为秒）
    print(f"扫描窗口 {a.hours}h，宇宙 {len(syms)} 币：{syms}", flush=True)

    cells = {}   # (pattern, flow, horizon, half) -> [xs]
    # [h420c] autocommit=True：只读扫描不持有事务——否则 Python 处理大数据时连接
    # 会"idle in transaction">90s，被后端 DB LeakGuard 的 pg_terminate_backend 强杀
    # （实测 168h 扫描 7 币后连接全丢的根因）。
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            for sym in syms:
                try:
                    ts, mids, ofis = _load_coin(cur, sym, t0, t1)
                except Exception as e:
                    print(f"  {sym}: 数据加载失败（跳过）{e}", flush=True)
                    continue
                n_ev = 0
                for i in range(60, len(ts) - 60):
                    pats = _detect(ts, mids, i)
                    if not pats:
                        continue
                    ofi = ofis[i]
                    base = mids[i]
                    if base <= 0:
                        continue
                    for pname, d in pats:
                        fo = ofi * d
                        flow = "with" if fo >= FLOW_T else ("against" if fo <= -FLOW_T else "neutral")
                        hf = 0 if ts[i] < half_s else 1
                        for steps, hname in HORIZONS:
                            j = i + steps
                            if j >= len(mids) or mids[j] <= 0:
                                continue
                            fwd = (mids[j] - base) / base * 1e4 * d
                            key = (pname, flow, hname, hf)
                            cells.setdefault(key, []).append(fwd)
                        n_ev += 1
                print(f"  {sym}: 事件 {n_ev}", flush=True)

    # 汇总：全窗 + 双半窗
    cards = []
    for (pname, flow, hname, hf), xs in sorted(cells.items()):
        pass
    # 按 (pname, flow, hname) 聚合
    agg = {}
    for (pname, flow, hname, hf), xs in cells.items():
        agg.setdefault((pname, flow, hname), {})[hf] = xs
    for (pname, flow, hname), halves in sorted(agg.items()):
        full = halves.get(0, []) + halves.get(1, [])
        st_full = _t_stat(full)
        st0 = _t_stat(halves.get(0, []))
        st1 = _t_stat(halves.get(1, []))
        if not st_full or st_full[0] < MIN_EVENTS_CELL:
            continue
        # [h420] 晋升门槛：双半窗各 ≥30 事件、|t|≥2、同号
        promote = (st0 and st1 and st0[0] >= 30 and st1[0] >= 30
                   and abs(st0[2]) >= T_HALF and abs(st1[2]) >= T_HALF
                   and (st0[1] > 0) == (st1[1] > 0))
        violation = (flow == "against" and st_full[1] > 0)
        card = {
            "pattern": pname, "flow": flow, "horizon": hname,
            "n": st_full[0], "mean_bp": round(st_full[1], 3), "t": round(st_full[2], 2),
            "half0": None if not st0 else {"n": st0[0], "mean_bp": round(st0[1], 3),
                                           "t": round(st0[2], 2)},
            "half1": None if not st1 else {"n": st1[0], "mean_bp": round(st1[1], 3),
                                           "t": round(st1[2], 2)},
            "oospass": bool(promote),
            "flow_law_violation": bool(violation and promote),
        }
        cards.append(card)

    cards.sort(key=lambda c: -(abs(c["t"]) if c["oospass"] else 0))
    res = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "window_hours": a.hours, "symbols": syms, "cards": cards}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n候选卡 {len(cards)} 张（OOS 双半窗通过 {sum(1 for c in cards if c['oospass'])} 张）")
    for c in cards[:25]:
        flag = "✓OOS" if c["oospass"] else "     "
        viol = " ⚠流律违反" if c.get("flow_law_violation") else ""
        print(f"  {flag} {c['pattern']:<4} {c['flow']:<8} {c['horizon']:<5} "
              f"n={c['n']:>5} mean={c['mean_bp']:>+7.3f}bp t={c['t']:>+5.1f}{viol}")
    print(f"已存 {OUT}")

    if not a.dry_run:
        _sec = [f"\n## [h420 自动发现候选 · {dt.datetime.now():%Y-%m-%d %H:%M}] 扫描卡（P1 流水线）\n"]
        for c in cards:
            if not c["oospass"]:
                continue
            viol = "（⚠ 统一流定律违反——against 为正，需三窗复现后人工评审）" \
                if c.get("flow_law_violation") else ""
            _sec.append(
                f"- {c['pattern']}×{c['flow']}×{c['horizon']}：全窗 n={c['n']} "
                f"mean={c['mean_bp']:+.3f}bp t={c['t']:+.1f}；半窗 "
                f"{c['half0']['t']:+.1f}/{c['half1']['t']:+.1f}（OOS 双窗 ✓）{viol}")
        if BACKLOG.exists():
            with open(BACKLOG, "a", encoding="utf-8") as f:
                f.write("\n".join(_sec) + "\n")
            print(f"已追加 {len(_sec) - 1} 张 OOS 通过卡到 {BACKLOG.name}")
    else:
        print("[dry-run] 未写 h375 清单")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
