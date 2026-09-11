# -*- coding: utf-8 -*-
"""「先盈利后大亏」观察期审计（第二十一轮）。

对应验收标准 §23 #10/#12：周期复跑，判断本轮修复是否真的把该模式压下去。
口径（与 §30.1 修正后一致）：**总 USD = unrealized + partial_realized − partial_fee**，
峰值用 `paper_positions.peak_pnl_pct`（价格口径小数）。

判定：
  - 模式交易 = 峰值 ≥ 0.5% 且 总 USD < 0；
  - 大亏 = 总 USD / 原始名义 ≤ -2%；
  - 按 tier（mid/long）与平仓通道分组。

用法：
  .venv\\Scripts\\python.exe backend/scripts/audit_profit_giveback.py [--days 14]
输出：`data/profit_giveback_audit.json` + 控制台摘要。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
OUT = ROOT / "data" / "profit_giveback_audit.json"


def total_usd(row) -> float:
    """总 USD：未实现 + 已实现（部分平仓） − 分批费用。"""
    return (float(row.get("unrealized_pnl") or 0)
            + float(row.get("partial_realized_pnl") or 0)
            - float(row.get("partial_fee_paid") or 0))


def source_family(strategy_id: str) -> str:
    """strategy_id → 入场来源家族（与 §34/Z20 分析口径一致，纯函数便于测试）。

    来源家族是「入场选择」的唯一可直接封堵的粒度；本轮实测：毒性家族
    （tpl_mid_reversion/tpl_mid_range）的毒性子集**已被 learned 门覆盖**，
    封堵家族本身反而更差（§34.3），故这里只做**观测**不做拦截。
    """
    s = (strategy_id or "").lower()
    for pref, fam in (
        ("tpl_mid_reversion", "mid_reversion"), ("tpl_mid_range", "mid_range"),
        ("tpl_mid_swing", "mid_swing"), ("tpl_mid_bull", "mid_bull"),
        ("tpl_long_swing", "long_swing"), ("tpl_long_mean_reversion", "long_reversion"),
        ("tpl_long", "long_other"), ("tpl_pro", "pro"), ("gen_", "gen"),
        ("auto_", "auto"), ("trend_e1", "trend_e1"), ("scalp", "scalp"),
    ):
        if s.startswith(pref):
            return fam
    return "other"


def summarize(rows, threshold_peak=0.5, big_loss_pct=-2.0):
    """把成交行聚合成审计指标（纯函数，便于测试）。

    rows: [{"tier","symbol","peak_pnl_pct","notional0","usd","close_reason","opened_at",
            "allow"(可选：门判定)}]

    返回键（新增于第二十三轮，旧键保持兼容）：
      by_tier[t] = {n, usd, win_rate, big_loss_n, pattern_n, pattern_usd,
                    pattern_rate, giveback_pct_sum, median_giveback_pct}
      gate = {"allow": {...}, "block": {...}}（仅当行上带 allow 时）
      verdicts = [{"name","ok","detail"}]
    """
    by_tier = defaultdict(lambda: {"n": 0, "usd": 0.0, "pattern_n": 0, "pattern_usd": 0.0,
                                   "big_loss_n": 0, "win_n": 0, "giveback": [],
                                   "allow": None, "gate_n": 0, "gate_pattern_n": 0,
                                   "gate_usd": 0.0, "gate_pattern_usd": 0.0})
    pattern_rows = []
    for r in rows:
        t = str(r.get("tier") or "?")
        b = by_tier[t]
        b["n"] += 1
        b["usd"] += float(r["usd"])
        if float(r["usd"]) > 0:
            b["win_n"] += 1
        ntl = float(r.get("notional0") or 0)
        if ntl > 0 and float(r["usd"]) / ntl * 100 <= big_loss_pct:
            b["big_loss_n"] += 1
        peak = float(r.get("peak_pnl_pct") or 0) * 100
        allow = r.get("allow")
        if allow is not None:
            b["gate_n"] += 1
            b["gate_usd"] += float(r["usd"])
        if peak >= threshold_peak and float(r["usd"]) < 0:
            b["pattern_n"] += 1
            b["pattern_usd"] += float(r["usd"])
            if ntl > 0:
                b["giveback"].append(peak - float(r["usd"]) / ntl * 100)
            pattern_rows.append(r)
            if allow is not None:
                b["gate_pattern_n"] += 1
                b["gate_pattern_usd"] += float(r["usd"])
    out = {"by_tier": {}, "pattern_n": len(pattern_rows),
           "pattern_usd": round(sum(float(r["usd"]) for r in pattern_rows), 2)}
    for t, b in by_tier.items():
        gb = sorted(b["giveback"])
        sub_t = [r for r in rows if str(r.get("tier") or "?") == t and r.get("allow") is not None]
        gs = {}
        for label, flag in (("allow", True), ("block", False)):
            s2 = [r for r in sub_t if r.get("allow") is flag]
            if not s2:
                continue
            pat2 = [r for r in s2
                    if float(r.get("peak_pnl_pct") or 0) * 100 >= threshold_peak
                    and float(r["usd"]) < 0]
            gs[label] = {"n": len(s2), "usd": round(sum(float(r["usd"]) for r in s2), 2),
                         "pattern_n": len(pat2),
                         "pattern_rate": round(len(pat2) / len(s2), 3),
                         "pattern_usd": round(sum(float(r["usd"]) for r in pat2), 2)}
        out["by_tier"][t] = {
            "n": b["n"], "usd": round(b["usd"], 2),
            "win_rate": round(b["win_n"] / b["n"], 3) if b["n"] else None,
            "big_loss_n": b["big_loss_n"],
            "pattern_n": b["pattern_n"], "pattern_usd": round(b["pattern_usd"], 2),
            "pattern_rate": round(b["pattern_n"] / b["n"], 3) if b["n"] else None,
            "giveback_pct_sum": round(sum(gb), 2),
            "median_giveback_pct": round(gb[len(gb) // 2], 2) if gb else None,
            "gate_split": gs,
        }
    out["worst5"] = [
        {"symbol": r["symbol"], "tier": r["tier"], "peak_pct": round(float(r["peak_pnl_pct"]) * 100, 2),
         "usd": round(float(r["usd"]), 2), "reason": str(r.get("close_reason") or "")[:28]}
        for r in sorted(pattern_rows, key=lambda x: float(x["usd"]))[:5]
    ]
    # 门放行 / 门拦截 对照（仅在行上带 allow 时）
    gate = {}
    for label, flag in (("allow", True), ("block", False)):
        sub = [r for r in rows if r.get("allow") is flag]
        if not sub:
            continue
        n = len(sub)
        pat = [r for r in sub
               if float(r.get("peak_pnl_pct") or 0) * 100 >= threshold_peak and float(r["usd"]) < 0]
        gate[label] = {
            "n": n, "usd": round(sum(float(r["usd"]) for r in sub), 2),
            "win_rate": round(sum(1 for r in sub if float(r["usd"]) > 0) / n, 3),
            "pattern_n": len(pat), "pattern_rate": round(len(pat) / n, 3),
            "pattern_usd": round(sum(float(r["usd"]) for r in pat), 2),
            "big_loss_n": sum(1 for r in sub
                              if float(r.get("notional0") or 0) > 0
                              and float(r["usd"]) / float(r["notional0"]) * 100 <= big_loss_pct),
        }
    if gate:
        out["gate"] = gate
    # 来源家族（strategy_id 前缀）——「入场选择」粒度，仅观测不拦截
    fam = defaultdict(lambda: {"n": 0, "usd": 0.0, "pat_n": 0, "pat_usd": 0.0, "win": 0})
    for r in rows:
        f = r.get("family")
        if not f:
            continue
        b = fam[f]
        b["n"] += 1
        b["usd"] += float(r["usd"])
        if float(r["usd"]) > 0:
            b["win"] += 1
        if float(r.get("peak_pnl_pct") or 0) * 100 >= threshold_peak and float(r["usd"]) < 0:
            b["pat_n"] += 1
            b["pat_usd"] += float(r["usd"])
    if fam:
        out["by_family"] = {
            f: {"n": b["n"], "usd": round(b["usd"], 2),
                "win_rate": round(b["win"] / b["n"], 3),
                "pattern_n": b["pat_n"],
                "pattern_rate": round(b["pat_n"] / b["n"], 3),
                "pattern_usd": round(b["pat_usd"], 2)}
            for f, b in sorted(fam.items(), key=lambda x: x[1]["usd"])
        }
    out["verdicts"] = _verdicts(out)
    return out


def _verdicts(rep):
    """验收判定（§23 #10 + 第二十三轮新增门判别线）。纯函数。"""
    v = []
    mid = rep["by_tier"].get("mid") or {}
    v.append({"name": "mid 模式 USD ≥ 0", "ok": (mid.get("pattern_usd") or 0) >= 0,
              "detail": f"mid pattern_usd={mid.get('pattern_usd')}"})
    v.append({"name": "mid 大亏笔数 ≤ 8", "ok": (mid.get("big_loss_n") or 0) <= 8,
              "detail": f"mid big_loss_n={mid.get('big_loss_n')}"})
    g = rep.get("gate") or {}
    if g.get("allow") and g.get("block"):
        a, b = g["allow"], g["block"]
        v.append({"name": "门放行集模式率 < 门拦截集",
                  "ok": (a["pattern_rate"] < b["pattern_rate"]),
                  "detail": f"allow={a['pattern_rate']} vs block={b['pattern_rate']}"})
        v.append({"name": "门放行集模式 USD/笔 优于门拦截集",
                  "ok": (a["pattern_usd"] / max(a["n"], 1)) >= (b["pattern_usd"] / max(b["n"], 1)),
                  "detail": f"allow={a['pattern_usd']}/{a['n']} vs block={b['pattern_usd']}/{b['n']}"})
    # long 车道：门**不覆盖**（生产 MIDLONG_LONG_LEARNED_TIERS=mid），此处仅展示
    # 「若覆盖会怎样」的反事实，用于持续验证豁免是否正确（§32.1：覆盖会拦掉盈利单）。
    lng = (rep["by_tier"].get("long") or {}).get("gate_split") or {}
    if lng.get("allow") and lng.get("block"):
        la, lb = lng["allow"], lng["block"]
        v.append({"name": "long 车道豁免正确（反事实：被拦集更赚）",
                  "ok": (lb["usd"] / max(lb["n"], 1)) > (la["usd"] / max(la["n"], 1)),
                  "detail": f"long 放行 ${la['usd']}/{la['n']} vs 拦截 ${lb['usd']}/{lb['n']}"})
    return v



def _pick_latest(series, sym):
    """挑该币种时间最新的 1h/1d 序列（默认 pick 只按长度，可能选到过期序列）。"""
    best = None
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 300 and (best is None or v[-1][0] > best[-1][0]):
            best = v
    return best


def attach_gate(rows):
    """给每行补 `allow`（learned 门判定，与生产 `_long_learned_ok` 同口径）。

    指标取**入场那一根已收盘 1h 之前**的特征（与 §33 分析脚本一致，无未来函数）。
    """
    try:
        from deep_long_freshness import build_bar_features, learned_ok_prod, load_klines
    except Exception as e:  # pragma: no cover - 依赖缺失时降级
        print(f"[gate] 跳过门判定：{e}")
        return 0
    syms = {r["symbol"] for r in rows}
    h1, d1 = load_klines(syms)
    cache = {}
    for sym in syms:
        s = _pick_latest(h1, sym)
        ds = _pick_latest(d1, sym)
        if not s or not ds or len(s) < 300 or len(ds) < 70:
            continue
        cache[sym] = (s, build_bar_features(s, ds))
    n_ok = 0
    for r in rows:
        c = cache.get(r["symbol"])
        ts = r.get("opened_ts") or 0
        if not c or not ts:
            continue
        s, (reg, pos, chg) = c
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0 or abs(s[i][0] - ts) > 7200:
            continue
        r["allow"] = bool(learned_ok_prod(reg[i], pos[i], chg[i]))
        n_ok += 1
    print(f"[gate] 已判定 {n_ok}/{len(rows)} 笔（learned 门，与生产同口径）")
    return n_ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--no-gate", action="store_true",
                    help="跳过门判定（不连行情库/不加载特征）")
    args = ap.parse_args()

    # [§92 修复 2026-09-11 / 缺陷 #75] 账户口径：业绩一律按活跃 PAPER 账户（默认 14）
    from backend.config.audit_scope import account_clause, describe_scope
    ACCT = account_clause()
    print(describe_scope())

    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        raw = [dict(r._mapping) for r in c.execute(text(f"""
            select symbol, side, timeframe_tier as tier, entry_price, close_price,
                   strategy_id, original_size, size, peak_pnl_pct, unrealized_pnl,
                   partial_realized_pnl, partial_fee_paid, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'{ACCT}
              and closed_at >= now() - interval '{int(args.days)} days'
            order by opened_at
        """)).fetchall()]

    rows = []
    for r in raw:
        entry = float(r["entry_price"] or 0)
        sz0 = float(r["original_size"] or r["size"] or 0)
        if entry <= 0 or sz0 <= 0:
            continue
        rows.append({
            "tier": str(r["tier"]), "symbol": r["symbol"],
            "strategy_id": str(r["strategy_id"] or ""),
            "family": source_family(str(r["strategy_id"] or "")),
            "peak_pnl_pct": float(r["peak_pnl_pct"] or 0),
            "notional0": entry * sz0,
            "usd": total_usd(r),
            "close_reason": str(r["close_reason"] or ""),
            "opened_at": str(r["opened_at"])[:19],
            "opened_ts": int(r["opened_at"].timestamp()) if r["opened_at"] else 0,
        })

    if not args.no_gate:
        attach_gate(rows)

    rep = summarize(rows)
    rep.update({"generated_at": datetime.now(timezone.utc).isoformat(),
                "days": args.days, "n": len(rows)})

    print(f"=== 「先盈利后大亏」审计（近 {args.days} 天）===")
    print(f"样本 n={len(rows)}")
    for t, b in sorted(rep["by_tier"].items()):
        print(f"  {t:<5} n={b['n']:>3} 总USD={b['usd']:>+9.2f} 胜率={b['win_rate']} "
              f"大亏(≤-2%)={b['big_loss_n']:>2} 模式={b['pattern_n']:>2}笔"
              f"({b['pattern_rate']}) {b['pattern_usd']:>+8.2f} "
              f"回吐合计={b['giveback_pct_sum']:>+7.2f}% 中位={b['median_giveback_pct']}%")
    if rep.get("gate"):
        print("\n门放行 / 门拦截 对照（learned 门，与生产同口径）：")
        for k in ("allow", "block"):
            g = rep["gate"].get(k)
            if not g:
                continue
            print(f"  {k:<6} n={g['n']:>3} 总USD={g['usd']:>+9.2f} 胜率={g['win_rate']} "
                  f"模式={g['pattern_n']:>2}笔({g['pattern_rate']}) {g['pattern_usd']:>+8.2f} "
                  f"大亏={g['big_loss_n']}")
        for t, b in sorted(rep["by_tier"].items()):
            gs = b.get("gate_split") or {}
            if not gs:
                continue
            parts = " ".join(f"{k}:n={v['n']},USD={v['usd']:+.2f},模式率={v['pattern_rate']}"
                             for k, v in sorted(gs.items()))
            print(f"  [{t}] {parts}")
    if rep.get("by_family"):
        print("\n来源家族（strategy_id 前缀；仅观测不拦截，§34.3 已证封堵无增量）：")
        print(f"  {'家族':<18}{'n':>4}{'总USD':>10}{'胜率':>7}{'模式n':>7}{'模式率':>8}{'模式USD':>10}")
        for f, b in rep["by_family"].items():
            print(f"  {f:<18}{b['n']:>4}{b['usd']:>+10.2f}{b['win_rate']:>7.3f}"
                  f"{b['pattern_n']:>7}{b['pattern_rate']:>8.3f}{b['pattern_usd']:>+10.2f}")
    print(f"\n模式合计：{rep['pattern_n']} 笔 / USD {rep['pattern_usd']:>+.2f}")
    print("最差 5 笔：")
    for w in rep["worst5"]:
        print(f"  {w['symbol']:<8} {w['tier']:<5} 峰值={w['peak_pct']:>+6.2f}% "
              f"USD={w['usd']:>+8.2f} {w['reason']}")
    print("\n验收判定：")
    for v in rep["verdicts"]:
        print(f"  [{'达标' if v['ok'] else '未达标'}] {v['name']} — {v['detail']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {OUT}")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
