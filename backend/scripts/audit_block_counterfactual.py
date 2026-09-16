# -*- coding: utf-8 -*-
"""[调研轮18 2026-09-16] **被拦截提案的反事实收益**（首个用例：midlong_cooldown_block）。

## 为什么需要

`midlong_cooldown_block` 是 24h 头号拦截（439 次）。冷却的自我说明是"避免信号未变时
重复付手续费"，但**没有任何数据证明被拦住的那些再入场是亏的**；而它同时也会拦掉
真正该做的单（实测 72h 内 619 行、全部 mid/全部 long，其中 live 536 / paper 83）。

本工具回答：**如果当时放行，按"下一根 K 线开盘价入场"看，这些单在 1h/4h/12h/24h 后
是赚是亏**（含 MFE/MAE 路径），并按冷却的**触发来源**分组（tier 冷却 / 上次平仓原因）。

## 口径与限制（务必与结论一起引用）

* 入场价取"拦截时刻所在 K 线之后的下一根 1h 开盘价"（无前视）；周期为 1h。
* 前向收益 = 期限末收盘 / 入场 − 1（按方向取符号），再扣往返成本（默认 8bp）。
* **这是"入场时刻"的反事实，不含后续出场逻辑**（真实系统有 2% 硬止损 + 分段止盈 +
  追踪）⇒ 收益被系统性高估的可能是存在的，故同时给出 MAE（最大逆行）：若多数样本
  MAE 深于 2%，说明真实系统会在中途被止损，前向收益不能直接当净利。
* 同一段冷却会在审计里重复多行；按 (会话, symbol) 且间隔 ≤ `--episode-gap-min`
  合并为**一个事件**（取最早一行作为入场时刻），避免重复计数放大结论。

只读。用法：
  python backend/scripts/audit_block_counterfactual.py
  python backend/scripts/audit_block_counterfactual.py --reason-prefix midlong_cooldown_block --hours 72
  python backend/scripts/audit_block_counterfactual.py --horizons 1,4,12,24 --fee-bps 8
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _subtype(reason: str) -> str:
    """从拦截原因里解析冷却触发来源（便于分组对比）。"""
    r = str(reason or "")
    if "刚平多仓" in r or "刚平空仓" in r:
        return "tier_cooldown(刚平仓未满30min)"
    if "DB耐久冷却" in r:
        import re

        m = re.search(r"刚([^（(]{1,40}?)平(?:long|short)仓", r)
        if m:
            return f"db:{m.group(1)[:28]}"
        return "db:other"
    if r.startswith("location_gate_veto"):
        import re

        m = re.search(r"分位\s*(\d+(?:\.\d+)?)%", r)
        if m:
            pos = float(m.group(1))
            band = int(pos // 10) * 10
            return f"loc:p{band}-{band + 10}"
        return "loc:other"
    return "other"


def _parse_pnl(reason: str):
    import re

    m = re.search(r"pnl=(-?\d+(?:\.\d+)?)", str(reason or ""))
    try:
        return float(m.group(1)) if m else None
    except (TypeError, ValueError):
        return None


def _load_bars(symbol: str, period: str, count: int):
    from backend.services.market_data import get_kline_data

    try:
        rows = get_kline_data(symbol, period=period, count=count) or []
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] {symbol} {period} K 线获取失败: {exc}")
        return []
    out = []
    for r in rows:
        try:
            out.append({
                "ts": float(r.get("timestamp") or 0),
                "o": float(r.get("open") or 0),
                "h": float(r.get("high") or 0),
                "l": float(r.get("low") or 0),
                "c": float(r.get("close") or 0),
            })
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["ts"])
    return [b for b in out if b["c"] > 0]


def _entry_index(bars, t0: float, period_sec: float):
    """返回"下一根 K 线"的下标（无前视）：第一根 ts >= t0 的 K 线。"""
    for i, b in enumerate(bars):
        if b["ts"] >= t0:
            return i
    return None


def _fwd(bars, i0: int, period_sec: float, horizon_h: float, direction: int):
    """期限末收盘 / 入场 − 1（按方向）。"""
    steps = int(round(horizon_h * 3600.0 / period_sec))
    j = i0 + steps
    if j >= len(bars):
        return None
    entry = bars[i0]["o"]
    if entry <= 0:
        return None
    return direction * (bars[j]["c"] / entry - 1.0)


def _path(bars, i0: int, period_sec: float, horizon_h: float, direction: int):
    """[t0, t0+horizon] 内的 MFE / MAE（按方向；正=有利）。"""
    steps = int(round(horizon_h * 3600.0 / period_sec))
    j = min(i0 + steps, len(bars) - 1)
    seg = bars[i0:j + 1]
    if not seg:
        return None, None
    entry = seg[0]["o"]
    if entry <= 0:
        return None, None
    hi = max(b["h"] for b in seg)
    lo = min(b["l"] for b in seg)
    if direction > 0:
        return hi / entry - 1.0, lo / entry - 1.0
    return entry / lo - 1.0, entry / hi - 1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason-prefix", default="midlong_cooldown_block")
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--horizons", default="1,4,12,24")
    ap.add_argument("--fee-bps", type=float, default=8.0)
    ap.add_argument("--episode-gap-min", type=float, default=15.0)
    ap.add_argument("--period", default="1h")
    ap.add_argument("--count", type=int, default=400)
    ap.add_argument("--detail", type=int, default=0)
    args = ap.parse_args()

    horizons = [float(x) for x in str(args.horizons).split(",") if x.strip()]
    period_sec = {"1h": 3600.0, "15m": 900.0, "4h": 14400.0}.get(args.period, 3600.0)

    from backend.services.mlto.midlong_direction_audit import _iter_rows, audit_paths

    cut = time.time() - args.hours * 3600.0
    rows = [r for r in _iter_rows(audit_paths())
            if float(r.get("epoch") or 0) >= cut
            and str(r.get("reason") or "").startswith(args.reason_prefix)]
    print(f"审计窗口 {args.hours:g}h：`{args.reason_prefix}` 行数 {len(rows)}")
    if not rows:
        return 0

    # ── 合并为"冷却事件" ──
    by_key = defaultdict(list)
    for r in rows:
        by_key[(str(r.get("session_id")), str(r.get("symbol")).upper())].append(r)
    episodes = []
    for (sid, sym), rs in by_key.items():
        rs.sort(key=lambda x: float(x.get("epoch") or 0))
        cur = [rs[0]]
        for prev, nxt in zip(rs, rs[1:]):
            gap_min = (float(nxt.get("epoch") or 0) - float(prev.get("epoch") or 0)) / 60.0
            if gap_min <= args.episode_gap_min:
                cur.append(nxt)
            else:
                episodes.append(cur)
                cur = [nxt]
        episodes.append(cur)
    print(f"合并为 **{len(episodes)} 个冷却事件**（同会话+同币、间隔 ≤{args.episode_gap_min:g}min 视为同一段）")

    syms = sorted({str(e[0].get("symbol")).upper() for e in episodes})
    bars_map = {}
    for s in syms:
        b = _load_bars(s, args.period, args.count)
        if b:
            bars_map[s] = b
    print(f"K 线（{args.period}）可用 symbol: {sorted(bars_map)} / 需要 {len(syms)}")

    # ── 逐个事件计算反事实 ──
    per_h = {h: [] for h in horizons}
    stats = []
    pend = 0
    for ep in episodes:
        r0 = ep[0]
        sym = str(r0.get("symbol")).upper()
        bars = bars_map.get(sym)
        if not bars:
            continue
        t0 = float(r0.get("epoch") or 0)
        # 方向解析：**先看 dir 字段**（location/regime 类拦截的 action 常为 hold，
        # 只看 action 会一律误判成 short——本工具首版就踩过这个坑），再看 action。
        _dir = str(r0.get("dir") or "").strip().lower()
        _act = str(r0.get("action") or "").strip().lower()
        if _dir in ("long", "short"):
            direction = 1 if _dir == "long" else -1
        elif _act in ("buy", "long", "sell", "short"):
            direction = 1 if _act in ("buy", "long") else -1
        else:
            continue  # 方向不可知 ⇒ 无法做反事实，跳过（不猜）
        i0 = _entry_index(bars, t0, period_sec)
        if i0 is None or i0 + 1 >= len(bars):
            pend += 1
            continue
        fwd = {h: _fwd(bars, i0, period_sec, h, direction) for h in horizons}
        mfe, mae = _path(bars, i0, period_sec, max(horizons), direction)
        rec = {
            "ts": t0, "sym": sym, "sid": str(r0.get("session_id")), "dir": direction,
            "n_rows": len(ep), "subtype": _subtype(r0.get("reason")),
            "last_pnl": _parse_pnl(r0.get("reason")),
            "fwd": fwd, "mfe": mfe, "mae": mae,
        }
        stats.append(rec)
        for h in horizons:
            if fwd.get(h) is not None:
                per_h[h].append(fwd[h])

    print(f"可用于反事实的事件: {len(stats)}（{pend} 个因前向数据不足跳过）")
    if not stats:
        return 0

    fee = args.fee_bps / 10000.0

    def _agg(vals):
        if not vals:
            return "n=0"
        return (f"n={len(vals):<4} mean={statistics.mean(vals)*100:+6.2f}% "
                f"med={statistics.median(vals)*100:+6.2f}% "
                f"win={sum(1 for v in vals if v > 0)/len(vals)*100:5.1f}% "
                f"净(扣{fee*100:.2f}%)={statistics.mean(vals)*100 - fee*100:+6.2f}%")

    print("\n== 全样本：放行后前向收益（按方向）==")
    for h in horizons:
        print(f"   {h:>4.0f}h: {_agg(per_h[h])}")

    print("\n== 按冷却触发来源分组（24h 口径）==")
    h_last = max(horizons)
    grp = defaultdict(list)
    for s in stats:
        grp[s["subtype"]].append(s)
    for k, v in sorted(grp.items(), key=lambda kv: -len(kv[1])):
        vals = [x["fwd"][h_last] for x in v if x["fwd"].get(h_last) is not None]
        maes = [x["mae"] for x in v if x["mae"] is not None]
        extra = (f" | MAE均值={statistics.mean(maes)*100:+.2f}% "
                 f"深于2%比例={sum(1 for m in maes if m < -0.02)/len(maes)*100:.0f}%"
                 if maes else "")
        print(f"   {k:<34}{_agg(vals)}{extra}")

    print("\n== 按方向分组（24h 口径）==")
    gd = defaultdict(list)
    for s in stats:
        gd["long" if s["dir"] > 0 else "short"].append(s)
    for k, v in sorted(gd.items()):
        vals = [x["fwd"][h_last] for x in v if x["fwd"].get(h_last) is not None]
        maes = [x["mae"] for x in v if x["mae"] is not None]
        extra = (f" | MAE均值={statistics.mean(maes)*100:+.2f}% "
                 f"深于2%={sum(1 for m in maes if m < -0.02)/len(maes)*100:.0f}%"
                 if maes else "")
        print(f"   {k:<8}{_agg(vals)}{extra}")

    print("\n== 来源 × 方向（24h 口径，n≥2）==")
    gsd = defaultdict(list)
    for s in stats:
        gsd[(s["subtype"], "long" if s["dir"] > 0 else "short")].append(s)
    for (k, d), v in sorted(gsd.items(), key=lambda kv: -len(kv[1])):
        vals = [x["fwd"][h_last] for x in v if x["fwd"].get(h_last) is not None]
        if len(v) < 2:
            continue
        print(f"   {k:<22}{d:<7}{_agg(vals)}")

    print("\n== 按会话分组（24h 口径）==")
    gs = defaultdict(list)
    for s in stats:
        gs[s["sid"]].append(s)
    for k, v in sorted(gs.items(), key=lambda kv: -len(kv[1])):
        vals = [x["fwd"][h_last] for x in v if x["fwd"].get(h_last) is not None]
        print(f"   {k:<18}{_agg(vals)}")

    maes_all = [s["mae"] for s in stats if s["mae"] is not None]
    mfes_all = [s["mfe"] for s in stats if s["mfe"] is not None]
    if maes_all:
        print(f"\n路径（{h_last:g}h 内）: MFE均值={statistics.mean(mfes_all)*100:+.2f}% "
              f"MAE均值={statistics.mean(maes_all)*100:+.2f}% "
              f"MAE 深于 2%（会被真实 2% 硬止损打出）比例="
              f"{sum(1 for m in maes_all if m < -0.02)/len(maes_all)*100:.0f}%")

    # ── 对照：同窗口真实成交 ──
    try:
        from backend.database.connection import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        try:
            r = db.execute(text("""
                SELECT count(*) n, avg(unrealized_pnl) avg_pnl,
                       sum(CASE WHEN unrealized_pnl > 0 THEN 1 ELSE 0 END) wins
                FROM paper_positions
                WHERE account_id=14 AND timeframe_tier='mid' AND status='closed'
                  AND closed_at > now() - (:h * interval '1 hour')
            """), {"h": int(args.hours)}).mappings().first()
            if r and r["n"]:
                print(f"\n对照（同窗口真实 mid 平仓）: n={r['n']} 均值 ${float(r['avg_pnl']):+.2f} "
                      f"胜率 {int(r['wins'])/int(r['n'])*100:.1f}%")
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        print(f"\n[WARN] 对照数据读取失败: {exc}")

    if args.detail:
        print(f"\n== 事件明细（前 {args.detail} 条，按时间）==")
        for s in sorted(stats, key=lambda x: x["ts"])[-args.detail:]:
            t = time.strftime("%m-%d %H:%M", time.localtime(s["ts"]))
            f24 = s["fwd"].get(h_last)
            print(f"   {t} {s['sym']:<7}{'long' if s['dir'] > 0 else 'short':<6}"
                  f"rows={s['n_rows']:<3}{s['subtype'][:26]:<28}"
                  f"24h={('%+.2f%%' % (f24*100)) if f24 is not None else '  n/a ':<8}"
                  f"MAE={('%+.2f%%' % (s['mae']*100)) if s['mae'] is not None else 'n/a'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
