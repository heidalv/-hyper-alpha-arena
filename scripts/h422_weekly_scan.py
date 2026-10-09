# -*- coding: utf-8 -*-
"""H422 P3 每周全量扫描：168h 发现扫描 + 币签名刷新（vr60）+ P2 vwap 闸复测。

设计对应《随市进化系统设计_20260927.md》§3.2（每周全量扫描）。
三件套（全部只读，产物注入 h375）：
  1. 全量发现：调用 h420_discovery_scan --hours 168（流律族 P1/P3/P4/P5 的 OOS 卡）；
  2. 币签名：每币 vr60 = Var(r60)/(12×Var(r5))（5s 网格）——>1 动量族 / <1 反转族，
     与 h367 口径一致（BNB vr60 1.39 为最强动量币的基准）；
  3. 已上线闸复测：P2（vwap_revert_bp=2.0）条件边际——60s 成交 VWAP 偏离 ≥2bp
     事件 × OFI 流向桶 × f60/f120（h358 口径）——检测该闸的 edge 是否衰减；
     若 |t| 跌出显著或符号翻转 ⇒ 产出"复核/退役"卡。
用法: python scripts/h422_weekly_scan.py [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h422_weekly_cards.json"
BACKLOG = ROOT.parent / "研究结论" / "h375_phase2_backlog_20260927.md"
HOURS = 168.0
VWAP_DEV_BP = 2.0
FLOW_T = 0.3


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
    if n < 30:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return None
    return (n, m, m / math.sqrt(var / n))


def _load_grid5(cur, sym, t0, t1):
    """5s 桶末 mid 网格——book_ticker 全量（~9 天留存；深度表仅 65h 会掏空周扫
    前半窗——h420 首轮 168h 全事件落半窗 1 即此因）。[h422a] 与 h420e 同源：
    7.5M 行/币/168h，autocommit 连接 + 普通有序查询 + Python 降采样。"""
    cur.execute("""
        SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker
        WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
          AND bid_px>0 AND ask_px>bid_px
        ORDER BY event_ts_ms
    """, (sym + "USDT", t0, t1))
    grid = []
    last_b = None
    for ms, bid, ask in cur.fetchall():
        b = (int(ms) // 5000) * 5
        if b != last_b:
            grid.append((b, (float(bid) + float(ask)) / 2.0))
            last_b = b
        else:
            grid[-1] = (b, (float(bid) + float(ask)) / 2.0)
    return grid


def _coin_signatures(grid):
    """vr60 = Var(r60)/(12×Var(r5))，5s 网格。"""
    mids = [m for _, m in grid]
    r5 = []
    r60 = []
    for i in range(1, len(mids)):
        if mids[i - 1] <= 0:
            continue
        r5.append((mids[i] - mids[i - 1]) / mids[i - 1])
        if i >= 12:
            r60.append((mids[i] - mids[i - 12]) / mids[i - 12])
    if len(r5) < 1000 or len(r60) < 1000:
        return None
    v1 = sum((x - sum(r5) / len(r5)) ** 2 for x in r5) / (len(r5) - 1)
    v60 = sum((x - sum(r60) / len(r60)) ** 2 for x in r60) / (len(r60) - 1)
    vr60 = (v60 / (12.0 * v1)) if v1 > 0 else 0.0
    fam = "动量族" if vr60 >= 1.0 else "反转族"
    return {"vr60": round(vr60, 3), "family": fam, "n5": len(r5)}


def _vwap_recheck(cur, sym, grid, t0, t1):
    """P2 复测：|60s VWAP 偏离|≥2bp 事件 × OFI 流向桶 × f60/f120。"""
    cells = {}
    cur.execute("""
        SELECT event_ts_ms/1000 AS t, price, qty FROM asterdex_trades
        WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
        ORDER BY event_ts_ms
    """, (sym + "USDT", t0, t1))
    tr = cur.fetchall()
    if len(tr) < 2000 or len(grid) < 2000:
        return cells
    # 15s 桶 OFI
    cur.execute("""
        SELECT timestamp, COALESCE(SUM(taker_buy_notional),0), COALESCE(SUM(taker_sell_notional),0)
        FROM market_trades_aggregated
        WHERE exchange='asterdex' AND symbol=%s AND timestamp >= %s AND timestamp <= %s
        GROUP BY timestamp ORDER BY timestamp
    """, (sym, t0, t1))
    ofi = {}
    for ts_ms, bv, sv in cur.fetchall():
        tot = float(bv) + float(sv)
        if tot > 0:
            ofi[int(ts_ms) // 1000] = (float(bv) - float(sv)) / tot
    # trades 前缀和（时间升序）按秒
    ts_t = [r[0] for r in tr]
    pv = [float(r[1]) * float(r[2]) for r in tr]
    qv = [float(r[2]) for r in tr]
    import bisect
    for i, (t, mid) in enumerate(grid):
        if mid <= 0 or i < 12:
            continue
        j = bisect.bisect_right(ts_t, t)
        k = bisect.bisect_right(ts_t, t - 60)
        if j <= k:
            continue
        spv = sum(pv[k:j])
        sqv = sum(qv[k:j])
        if sqv <= 0:
            continue
        vwap = spv / sqv
        dev = (mid - vwap) / mid * 1e4
        if abs(dev) < VWAP_DEV_BP:
            continue
        d = -1.0 if dev > 0 else 1.0          # 回归方向
        fo = ofi.get((t // 15) * 15, 0.0) * d
        flow = "with" if fo >= FLOW_T else ("against" if fo <= -FLOW_T else "neutral")
        for steps, hname in ((12, "f60"), (24, "f120")):
            j2 = i + steps
            if j2 >= len(grid) or grid[j2][1] <= 0:
                continue
            fwd = (grid[j2][1] - mid) / mid * 1e4 * d
            cells.setdefault((flow, hname), []).append(fwd)
    out = {}
    for (flow, hname), xs in sorted(cells.items()):
        st = _t(xs)
        if st:
            out[f"{flow}_{hname}"] = {"n": st[0], "mean_bp": round(st[1], 3),
                                      "t": round(st[2], 2)}
    return out


def main() -> int:
    # [h422d] Windows 控制台 GBK 打不出 ✓/⚠——强制 UTF-8 输出
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            m = cur.fetchone()[0]
            syms = [str(s) for s in (m.get("symbols") or []) if str(s)]

    t1 = int(dt.datetime.now(dt.timezone.utc).timestamp()) * 1000
    t0 = t1 - int(HOURS * 3600 * 1000)

    # 1) 全量发现（复用 P1 扫描器；--dry-run 只写 JSON 不重复写 BACKLOG）。
    #    [h422a] dry-run 不再调用子进程——h420 会覆写日常 24h JSON，破坏 dry-run 纯净性。
    #    [h422b] 子进程输出重定向到日志文件而非管道——沙箱下管道捕获会 EPERM，
    #    schtasks 直跑也要留日志。
    DISC = ROOT / "research_l1" / "out" / "h420_discovery_cards.json"
    SUB_LOG = ROOT / "research_l1" / "out" / "h422_sub_h420.log"
    if not a.dry_run:
        with open(SUB_LOG, "a", encoding="utf-8") as f:
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "h420_discovery_scan.py"),
                                "--hours", str(HOURS), "--dry-run"],
                               cwd=str(ROOT), stdout=f, stderr=f)
        if r.returncode != 0:
            print(f"[warn] h420 子进程 rc={r.returncode}（见 {SUB_LOG.name}）", flush=True)
        disc = json.loads(DISC.read_text(encoding="utf-8")) \
            if r.returncode == 0 and DISC.exists() else {}
    else:
        disc = json.loads(DISC.read_text(encoding="utf-8")) if DISC.exists() else {}

    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                         autocommit=True) as cm:
        with cm.cursor() as cur:
            sigs = {}
            vwap = {}
            for sym in syms:
                try:
                    grid = _load_grid5(cur, sym, t0, t1)
                except Exception as e:
                    print(f"  {sym}: 网格加载失败（跳过）{e}", flush=True)
                    sigs[sym] = None
                    continue
                sigs[sym] = _coin_signatures(grid)
                vwap.update(_vwap_recheck(cur, sym, grid, t0, t1))

    print("== 币签名（vr60）==")
    for sym, s in sorted(sigs.items()):
        if s:
            print(f"  {sym:<6} vr60={s['vr60']:>5.2f} {s['family']} (n={s['n5']})")
        else:
            print(f"  {sym:<6} 数据不足")
    print("== P2 vwap 复测（|偏离|≥2bp × OFI）==")
    for k, v in sorted(vwap.items()):
        print(f"  {k:<14} n={v['n']:>6} mean={v['mean_bp']:>+7.3f}bp t={v['t']:>+5.1f}")
    print(f"== 全量发现：{len(disc.get('cards', []))} 格，"
          f"OOS {sum(1 for x in disc.get('cards', []) if x.get('oospass'))} 张 ==")

    res = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "window_hours": HOURS, "symbols": syms,
           "signatures": sigs, "vwap_recheck": vwap,
           "discovery": disc.get("cards", [])}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    # P2 复测裁决：with 桶显著为正 = 闸仍有效；转负 = 退役级复核卡；
    # [h422c] 失显著（0<mean 但 |t|<2）= 复核级卡——设计 §3.2"边际收益失显著 ⇒ 复核/退役"。
    notes = []
    w_f60 = vwap.get("with_f60")
    a_f60 = vwap.get("against_f60")
    if w_f60 and w_f60["mean_bp"] <= 0:
        notes.append(f"⚠ P2 vwap 复测：with_f60 {w_f60['mean_bp']:+.3f}bp(t={w_f60['t']:+.1f})"
                     " 转非正——闸 edge 疑似衰减，产出复核卡（人工评审）")
    elif w_f60 and abs(w_f60["t"]) < 2.0:
        notes.append(f"⚠ P2 vwap 复测：with_f60 {w_f60['mean_bp']:+.3f}bp(t={w_f60['t']:+.1f})"
                     " 失显著——边际收益衰减，产出复核卡（人工评审）")
    else:
        notes.append(f"✓ P2 vwap 复测：with_f60 {w_f60['mean_bp']:+.3f}bp(t={w_f60['t']:+.1f})"
                     if w_f60 else "P2 复测样本不足")
    if a_f60 and a_f60["mean_bp"] >= 0:
        notes.append(f"⚠ P2 vwap 复测：against_f60 {a_f60['mean_bp']:+.3f}bp 转非负"
                     "——流律例外，需三窗复核")

    if not a.dry_run:
        sec = [f"\n## [h422 每周全量扫描 · {dt.datetime.now():%Y-%m-%d %H:%M}]（P3）\n"]
        sec.append("- 币签名：")
        for sym, s in sorted(sigs.items()):
            sec.append(f"  - {sym}: vr60={s['vr60']} {s['family']}" if s else f"  - {sym}: 数据不足")
        sec.append("- P2 vwap 复测：")
        sec += [f"  - {n}" for n in notes]
        sec.append("- 全量发现卡数：" + str(len(disc.get("cards", []))))
        if BACKLOG.exists():
            with open(BACKLOG, "a", encoding="utf-8") as f:
                f.write("\n".join(sec) + "\n")
            print(f"已写入 {BACKLOG.name}")
    else:
        print("[dry-run] 未写入")
    print(f"已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
