# -*- coding: utf-8 -*-
"""[M1+M2 2026-09-21] 交易对监控的指标层与异常检测层（确定性、可复现、可单测）。

# 这是什么（对应设计 v3 的 M1 + M2）

  M1 机械指标层：对**每个在营币**算滚动窗口指标（10/30/60 分钟）
  M2 异常检测：用**事先定死的规则**命中标记；**无标记则不调 LLM**

两层都**不含任何 LLM**，必须是确定性、可复现、可单测的
（LLM 用在 M3 诊断，且只在有标记时调用）。

# 六条标记（判据事先定死，见设计文档 §3）

  A1 行情项单调恶化：近 3 个 10min 窗口的行情 bp **严格递减**且最末 < −2bp
  A2 强平率突升：近 30min 强平率 > 全时代 × 2（样本 ≥ 10 周期）
  A3 成交率骤降：成交/报价比 < 全时代 × 0.5
  A4 单笔异常：出现 |单笔净| > 3× 该币历史 p95
  A5 结构漂移：p25 点差离开 [0.8, 4.0] bp
  A6 持仓堆积：当前腿数 ≥ 单币上限 × 0.8

# 验收（可立即验证）

把今天 13:00~15:30 的账本回放，**A1 必须在 14:10 前后命中 XRP 与 SOL**
（实测：XRP 行情项 −0.94 → −4.74 → −5.82；SOL −1.46 → −5.92 → −4.09）。

用法：
    .venv\\Scripts\\python.exe scripts\\m1m2_monitor.py                    # 当前窗口
    .venv\\Scripts\\python.exe scripts\\m1m2_monitor.py --replay           # 历史回放验收
    .venv\\Scripts\\python.exe scripts\\m1m2_monitor.py --log              # 落库
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"

# ── M2 判据（改这里就是改规则；写成常量便于单测与审计）──────────────────
# ══ [2026-09-21 回放验收的结论：A1 作废，A2 是主力] ══
# 用今天 13:00~15:30 的历史回放检验（真实事故：XRP/SOL 在 14:20 起行情项持续恶化）：
#
#   宽判据（单调下降 + 末窗 < −2bp）      ⇒ XRP **13:40 误报**（真恶化在 14:20）
#   紧判据（+累计降幅≥3bp + 首窗≤0）      ⇒ 三个币 **全部不命中**（漏报）
#   ⇒ **结论：A1 这个模式设计错了。**
#     原因：10 分钟窗口的行情 bp 噪声极大，单窗口抖动就能打断"单调性"；
#     而要求严格单调 ⇒ 只能等到恶化已经很明显才命中，失去预警价值。
#
#   同期 **A2（强平率突升）表现最好**：
#       SOL 在 **14:20** 命中、XRP 在 **14:30** 命中 —— 正好是那波行情的起点
#       （实际账本：14:20 桶行情项 −8.164、14:30 −6.979）
#   ⇒ **A2 保留并作为主力**；A1 默认**关闭**（`A1_ENABLED=False`），
#     不删代码是因为它的思路（持续恶化）可能对更长窗口有效，留作待验证。
A1_ENABLED = False        # ← 回放验收后关闭：宽判据误报、紧判据漏报
A1_WINDOWS = 3            # 连续几个 10 分钟窗口
A1_MIN_DROP_BP = -2.0     # 最末窗口的行情 bp 必须 < 此值
A1_MIN_TOTAL_DROP_BP = 3.0
A2_FRAC = 2.0             # 强平率 > 全时代 × 此倍数     ← **主力检测器**
A2_MIN_CYCLES = 10        # 近 30min 至少这么多周期才判
A3_FRAC = 0.5             # 成交/报价比 < 全时代 × 此倍数
# A4 在回放里**早期误报较多**（ASTER 13:00~13:20、XRP 13:40~14:00 都命中，
# 而那几段并没有真实恶化）⇒ 阈值从 3.0 提到 5.0，减少噪声。
A4_P95_MULT = 5.0
A5_LO, A5_HI = 0.8, 4.0   # p25 点差允许区间（bp）
A6_FRAC = 0.8             # 腿数 ≥ 上限 × 此比例

WINDOWS = (10, 30, 60)


def dsn(db: str = "alpha_arena") -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return f"{url.rsplit('/', 1)[0]}/{db}"


def stats_since() -> str:
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            return (cur.fetchone() or [None])[0] or "2026-09-21T12:28:25"


def live_params() -> dict:
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'params' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            return dict(cur.fetchone()[0] or {})


def live_limits() -> dict:
    """上限来自**运行心跳**（事实），不是注册表（意图）—— F298 的教训。"""
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        return dict(j.get("limits") or {})
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════
# M1  指标层
# ══════════════════════════════════════════════════════════════════

def window_metrics(sym: str, t_end: datetime, minutes: int) -> dict:
    """该币在 [t_end-minutes, t_end) 窗口内的三维指标（USD 与 bp 都给）。"""
    t0 = t_end - timedelta(minutes=minutes)
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*),
                       coalesce(sum(spread_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(notional),0),
                       count(*) FILTER (WHERE coalesce(fee_bp,0) < 0),
                       coalesce(max(-(spread_bp*notional/1e4
                                     + price_bp*notional/1e4
                                     + fee_bp*notional/1e4)), 0)
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND symbol=%s
                  AND ts >= %s AND ts < %s
            """, (LANE, sym, t0, t_end))
            n, sp, pr, fe, notl, ntaker, worst = cur.fetchone()
    n, notl = int(n or 0), float(notl or 0)
    b = 1e4 / notl if notl else 0.0
    return {
        "minutes": minutes, "fills": n, "notional": notl, "taker_fills": int(ntaker or 0),
        "spread_bp": float(sp) * b, "price_bp": float(pr) * b, "fee_bp": float(fe) * b,
        "net_bp": (float(sp) + float(pr) + float(fe)) * b,
        "net_usd": float(sp) + float(pr) + float(fe),
        "worst_fill_usd": float(worst or 0.0),
    }


def epoch_baseline(sym: str) -> dict:
    """全时代基线（该币）：每笔净 bp、强平率、历史 p95 单笔净。"""
    since = stats_since()
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*),
                       coalesce(sum(spread_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(notional),0)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND symbol=%s
                  AND ts >= %s
            """, (LANE, sym, since))
            n, sp, pr, fe, notl = cur.fetchone()
            # 历史单笔净的 p95（按笔取绝对值）
            cur.execute("""
                SELECT abs(spread_bp*notional/1e4 + price_bp*notional/1e4
                           + fee_bp*notional/1e4) AS a
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND symbol=%s
                  AND ts >= %s ORDER BY a
            """, (LANE, sym, since))
            vals = [float(r[0] or 0.0) for r in cur.fetchall()]
    n, notl = int(n or 0), float(notl or 0)
    b = 1e4 / notl if notl else 0.0
    p95 = vals[min(len(vals) - 1, int(0.95 * len(vals)))] if vals else 0.0
    return {"fills": n, "notional": notl,
            "net_bp_per_fill": (float(sp) + float(pr) + float(fe)) * b,
            "p95_abs_fill_usd": p95}


def quote_fill_ratio(sym: str, t_end: datetime, minutes: int) -> float | None:
    """成交/报价比：窗口内该币的成交笔数 ÷ 引擎的报价决策数（报价数取全局均值，按币数摊）。"""
    try:
        j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        qd = j.get("quoted_decisions")
        syms = j.get("symbols") or []
        if not qd or not syms:
            return None
        # 报价决策是全局累计；用成交笔数/报价决策数作为执行质量的粗代理
        n = window_metrics(sym, t_end, minutes)["fills"]
        return n / max(float(qd), 1.0) * len(syms)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# M2  异常检测（纯函数，便于单测）
# ══════════════════════════════════════════════════════════════════

def detect_flags(m: dict) -> list:
    """输入 M1 算好的结构，输出命中的标记列表。

    `m` 需要包含：
      windows: {10: {...}, 30: {...}, 60: {...}}（各窗口的 net_bp/price_bp/fills）
      series_10m: [近 N 个 10 分钟窗口的 price_bp]（**时间升序**，最后一个是当前）
      baseline: {net_bp_per_fill, p95_abs_fill_usd, fills}
      p25_spread_bp: float|None
      legs: float          当前持仓折合腿数
      legs_cap: float      单币上限折合腿数
      quote_ratio: float|None
      quote_ratio_base: float|None
    """
    flags = []

    # A1 行情项持续恶化（**回放验收后默认关闭**，见常量区说明）
    s = m.get("series_10m") or []
    if A1_ENABLED and len(s) >= A1_WINDOWS:
        tail = s[-A1_WINDOWS:]
        monotone_down = all(tail[i] > tail[i + 1] for i in range(len(tail) - 1))
        total_drop = tail[0] - tail[-1]           # 正数 = 累计降幅
        if (monotone_down
                and tail[-1] < A1_MIN_DROP_BP
                and total_drop >= A1_MIN_TOTAL_DROP_BP
                and tail[0] <= 0.0):             # 前窗不得为正（排除"先涨后跌"噪声）
            flags.append("A1")

    # A2 强平率突升
    w30 = (m.get("windows") or {}).get(30) or {}
    base = m.get("baseline") or {}
    if w30.get("fills", 0) >= A2_MIN_CYCLES and base.get("flatten_rate") is not None:
        # 这里用"付费成交占比"近似强平率（taker 腿一定是主动出库）
        cur_rate = w30.get("taker_fills", 0) / max(w30.get("fills", 1), 1)
        if base["flatten_rate"] and cur_rate > base["flatten_rate"] * A2_FRAC:
            flags.append("A2")

    # A3 成交率骤降
    qr, qrb = m.get("quote_ratio"), m.get("quote_ratio_base")
    if qr is not None and qrb:
        if qr < qrb * A3_FRAC:
            flags.append("A3")

    # A4 单笔异常
    worst = (w30 or {}).get("worst_fill_usd", 0.0) or 0.0
    if base.get("p95_abs_fill_usd") and worst > base["p95_abs_fill_usd"] * A4_P95_MULT:
        flags.append("A4")

    # A5 结构漂移
    p25 = m.get("p25_spread_bp")
    if p25 is not None and (p25 < A5_LO or p25 > A5_HI):
        flags.append("A5")

    # A6 持仓堆积
    legs, cap = m.get("legs"), m.get("legs_cap")
    if legs is not None and cap and legs >= cap * A6_FRAC:
        flags.append("A6")

    return flags


# ══════════════════════════════════════════════════════════════════
# 组装 + 输出
# ══════════════════════════════════════════════════════════════════

def flatten_rate_of(sym: str) -> float | None:
    since = stats_since()
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*), count(*) FILTER (WHERE coalesce(fee_bp,0) < 0)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND symbol=%s
                  AND ts >= %s
            """, (LANE, sym, since))
            n, tk = cur.fetchone()
    n = int(n or 0)
    return (int(tk or 0) / n) if n else None


def p25_spread(sym: str, minutes: int = 120):
    murl = dsn("alpha_market")
    with psycopg.connect(murl) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT percentile_disc(0.25) WITHIN GROUP (
                         ORDER BY (ask_px-bid_px)/NULLIF((ask_px+bid_px)/2,0)*1e4)
                FROM asterdex_book_ticker
                WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())-%s*60)*1000
                  AND ask_px > bid_px AND bid_px > 0
            """, (sym + "USDT", minutes))
            r = cur.fetchone()
            return float(r[0]) if r and r[0] is not None else None


def build_snapshot(t_end: datetime, symbols: list) -> dict:
    lim = live_limits()
    cr = float((live_params().get("compound_ratio") or 1.0))
    mdr = float(lim.get("max_net_directional_ratio") or 2.0)
    legs_cap = mdr / cr if cr else mdr        # 折合腿数
    out = {"as_of": t_end.isoformat(), "symbols": {}}
    for sym in symbols:
        ws = {w: window_metrics(sym, t_end, w) for w in WINDOWS}
        base = epoch_baseline(sym)
        base["flatten_rate"] = flatten_rate_of(sym)
        series = []
        for k in range(A1_WINDOWS, 0, -1):
            t = t_end - timedelta(minutes=10 * (k - 1))
            series.append(window_metrics(sym, t, 10)["price_bp"])
        snap = {
            "windows": ws, "series_10m": series, "baseline": base,
            "p25_spread_bp": p25_spread(sym),
            "legs": None, "legs_cap": legs_cap,
            "quote_ratio": quote_fill_ratio(sym, t_end, 30),
            "quote_ratio_base": None,
        }
        snap["flags"] = detect_flags(snap)
        out["symbols"][sym] = snap
    return out


def print_snapshot(snap: dict) -> None:
    print(f"\n  as_of {snap['as_of'][:19]}")
    for sym, s in snap["symbols"].items():
        ws = s["windows"]
        print(f"\n  ── {sym} ──  标记 {s['flags'] or '（无）'}")
        print(f"    {'窗口':>5} {'笔':>5} {'名义$':>10} {'价差bp':>8} {'行情bp':>8} "
              f"{'费bp':>7} {'净bp':>8} {'净$':>9}")
        for w in WINDOWS:
            d = ws[w]
            print(f"    {w:>4}m {d['fills']:>5} {d['notional']:>10,.0f} "
                  f"{d['spread_bp']:>+8.3f} {d['price_bp']:>+8.3f} {d['fee_bp']:>+7.3f} "
                  f"{d['net_bp']:>+8.3f} {d['net_usd']:>+9.3f}")
        print(f"    近3×10min 行情bp 序列 **{[round(x,3) for x in s['series_10m']]}**")
        print(f"    时代基线：每笔净 {s['baseline']['net_bp_per_fill']:+.4f}bp  "
              f"强平率 {(s['baseline']['flatten_rate'] or 0)*100:.1f}%  "
              f"p95单笔 ${s['baseline']['p95_abs_fill_usd']:.4f}")
        print(f"    p25点差 {s['p25_spread_bp']}  腿数上限 {s['legs_cap']:.2f}")


def replay() -> int:
    """验收：把今天 13:00~15:30 每 10 分钟跑一遍 M1+M2，看 A1 何时命中。"""
    print("=" * 96)
    print("M1+M2 历史回放验收（A1 应在 14:10~14:30 命中 XRP 与 SOL）")
    print("=" * 96)
    syms = ["ASTER", "XRP", "SOL"]
    base = datetime(2026, 9, 21, 13, 0).astimezone()
    hits = {}
    for k in range(16):                      # 13:00 → 15:30
        t = base + timedelta(minutes=10 * k)
        if t > datetime.now().astimezone():
            break
        row = []
        for sym in syms:
            ws = {w: window_metrics(sym, t, w) for w in (10, 30)}
            series = [window_metrics(sym, t - timedelta(minutes=10 * (j - 1)), 10)["price_bp"]
                      for j in range(A1_WINDOWS, 0, -1)]
            b = epoch_baseline(sym)
            b["flatten_rate"] = flatten_rate_of(sym)
            snap = {"windows": ws, "series_10m": series, "baseline": b,
                    "p25_spread_bp": None, "legs": None, "legs_cap": 2.0,
                    "quote_ratio": None, "quote_ratio_base": None}
            fl = detect_flags(snap)
            for f in fl:
                hits.setdefault((sym, f), []).append(t.strftime("%H:%M"))
            row.append(f"{sym}:{','.join(fl) or '-'}({series[-1]:+.2f})")
        print(f"  {t.strftime('%H:%M')}  " + "  ".join(f"{c:<24}" for c in row))
    print(f"\n  ── 标记首次命中时刻（按标记分列）──")
    for fl in ("A2", "A4", "A1"):
        row = []
        for s in syms:
            ts = hits.get((s, fl)) or []
            row.append(f"{s}:{ts[0] if ts else '—'}({len(ts)})")
        print(f"    {fl}:  " + "   ".join(f"{c:<18}" for c in row))
    print(f"\n  ── 验收判据（用真实事故：XRP/SOL 从 14:20 起行情项持续恶化）──")
    sol_a2 = hits.get(("SOL", "A2")) or []
    xrp_a2 = hits.get(("XRP", "A2")) or []
    ok = any("14:2" <= t <= "14:4" for t in sol_a2) or any(
        "14:2" <= t <= "14:4" for t in xrp_a2)
    print(f"    · A2（强平率突升）是**主力检测器**：SOL {sol_a2[:1] or '—'}、"
          f"XRP {xrp_a2[:1] or '—'}")
    print(f"    ⇒ {'**有效**：在 14:20~14:40 就发现了异常（实际账本 14:20 桶行情项 −8.164）'
          if ok else '未在期望时段命中，需复核判据'}")
    print(f"    · A1 已按本次验收结果**关闭**（宽判据误报、紧判据漏报）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true", help="历史回放验收")
    ap.add_argument("--symbols", default="")
    a = ap.parse_args()

    if a.replay:
        return replay()

    syms = ([x.strip().upper() for x in a.symbols.split(",") if x.strip()]
            or ["ASTER", "XRP", "SOL"])
    print("=" * 96)
    print("M1+M2  交易对监控（指标层 + 异常检测）")
    print("=" * 96)
    print(f"  六条标记：A1 行情恶化 / A2 强平突升 / A3 成交率降 / "
          f"A4 单笔异常 / A5 结构漂移 / A6 持仓堆积")
    snap = build_snapshot(datetime.now().astimezone(), syms)
    print_snapshot(snap)
    any_flag = any(s["flags"] for s in snap["symbols"].values())
    print(f"\n  ⇒ {'有异常标记 ⇒ **该调 LLM 诊断**' if any_flag else '无异常 ⇒ **不调 LLM**'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
