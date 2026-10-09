# -*- coding: utf-8 -*-
r"""[F380 2026-09-18] 读数诚实性（§32–§35）的**改动前/后对照预览**（只读、不落盘、不生效）。

第 1 项待拍板（B18）此前只有"建议"，没有一个"看一眼就知道要不要开"的对照。
本脚本用**真实数据**把四处渲染的"现状 vs 按建议规则重渲染"并排打出来：

| 节 | 现状 | 预览（建议规则） |
|---|---|---|
| §32 `backtest_wisdom` | `回测最优夏普比率: 11.46` | 加 `⚠样本内最大值/样本存疑` 标注、不把极值当目标 |
| §33 `consolidated_lessons` | `3 笔 胜率67%`、`days=7`（实为 10.8 天） | 小样本打标、写出**实际跨度** |
| §34 主脑近期盈亏 | 毛口径（不扣费用） | 净口径（扣 `partial_fee_paid`），并列毛/净 |
| §35 `factor_route` | 票带 `ic` 无 `n`；`confidence`= \|score\| 映射 | 票加 `n`；`confidence` 注明非置信度 |

**本脚本不修改任何生产文件/配置**：它只读真实产物与库，并在本地实现"建议的渲染"。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def sec(t: str) -> None:
    print("\n" + "=" * 92)
    print(t)
    print("=" * 92)


# ───────────────────── §32 backtest_wisdom ─────────────────────

def p32() -> None:
    sec("§32 `backtest_wisdom`（主脑读到的『回测经验』）")
    try:
        from backend.services.mlto.brain import _wisdom_feed
        cur = (_wisdom_feed("preview", "mid") or {}).get("text") or ""
    except Exception as exc:
        print(f"  取当前文本失败: {str(exc)[:120]}")
        return
    if not cur.strip():
        print("  当前为空（无智慧可预览）")
        return
    print("  --- 现状（主脑实际读到的）---")
    for ln in cur.strip().splitlines():
        print(f"    {ln}")

    # 建议规则：极值加存疑标注；不把 max 当目标
    def annotate(ln: str) -> str:
        m = re.search(r"回测最优夏普比率:\s*([0-9.]+)", ln)
        if m and float(m.group(1)) > 5:
            return ln + "   ⚠样本内最大值，非预期；不据此加杠杆"
        m = re.search(r"回测最佳胜率:\s*([0-9.]+)%", ln)
        if m and float(m.group(1)) > 90:
            return ln + "   ⚠接近上限，通常为样本内巧合"
        return ln

    print("\n  --- 预览（按建议规则重渲染，仅文本层变化）---")
    for ln in cur.strip().splitlines():
        new = annotate(ln)
        print(f"    {new}{'   ← 变化' if new != ln else ''}")
    print("\n  说明：建议规则只加标注/不改数值；若采纳『不打印 best_sharpe』则整行消失。")


# ───────────────────── §33 consolidated_lessons ─────────────────────

def p33() -> None:
    sec("§33 `consolidated_lessons`（主脑读到的『长期规则』）")
    p = ROOT / "backend/data/analysis/latest_episodic_consolidation.json"
    if not p.exists():
        print("  无巩固产物，跳过")
        return
    d = json.loads(p.read_text(encoding="utf-8"))
    txt = str(d.get("lessons_text") or "")
    rules = d.get("rules") or []
    print(f"  产物 generated_at={d.get('generated_at')}  声称 days={d.get('days')}  "
          f"total_episodes={d.get('total_episodes')}")
    print("  --- 现状 ---")
    for ln in txt.splitlines():
        print(f"    {ln}")

    # 实际跨度（只读）
    span_line = ""
    try:
        from backend.services.mlto.db_models import MltoEpisode
        from backend.database.connection import AnalyticsSessionLocal
        with AnalyticsSessionLocal() as db:
            rows = (db.query(MltoEpisode).filter(MltoEpisode.opened == 1,
                                                 MltoEpisode.outcome_pct.isnot(None))
                    .order_by(MltoEpisode.created_at.desc()).limit(2000).all())
            ts = sorted(r.created_at for r in rows if r.created_at)
        if ts:
            span = (ts[-1] - ts[0]).total_seconds() / 86400
            import datetime as dt
            within = sum(1 for t in ts if t >= ts[-1] - dt.timedelta(days=int(d.get("days") or 7)))
            span_line = (f"[口径] 实际参与情景 {len(ts)} 条，跨度 {span:.1f} 天"
                         f"（声明 {d.get('days')} 天）；真正落在声明窗口内的 {within}/{len(ts)} 条")
    except Exception as exc:
        span_line = f"[口径] 无法计算实际跨度: {str(exc)[:80]}"

    def annotate(ln: str) -> str:
        m = re.search(r"：(\d+)\s*笔", ln)
        if m and int(m.group(1)) < 10:
            return ln + "   ⚠样本少（<10 笔），不足以作规则"
        return ln

    print("\n  --- 预览（小样本打标 + 写出口径行）---")
    if span_line:
        print(f"    {span_line}")
    for ln in txt.splitlines():
        new = annotate(ln)
        print(f"    {new}{'   ← 变化' if new != ln else ''}")
    small = [r for r in rules if int(r.get("n") or 0) < 10]
    print(f"\n  受影响规则数：{len(small)}/{len(rules)}（n<10）；"
          f"若把 min_samples 提到 10，其中 {len([r for r in rules if int(r.get('n') or 0) < 10])} 条会消失")


# ───────────────────── §34 主脑近期盈亏口径 ─────────────────────

def p34() -> None:
    sec("§34 主脑『近 14 天战绩』毛口径 vs 净口径")
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        db.execute(text("SET app.is_admin='on'"))
    except Exception as exc:
        print(f"  无 DB 会话: {str(exc)[:110]}")
        return
    try:
        for sym in ("BTC", "ETH", "SOL"):
            rows = db.execute(text("""
                SELECT side, size, entry_price, close_price, partial_realized_pnl,
                       COALESCE(partial_fee_paid, 0) AS fee
                FROM paper_positions
                WHERE symbol=:s AND status='closed'
                  AND closed_at >= now() - interval '14 days'
            """), {"s": sym}).mappings().all()
            gross = 0.0
            fees = 0.0
            n = 0
            for r in rows:
                e, c, sz = float(r["entry_price"] or 0), float(r["close_price"] or 0), float(r["size"] or 0)
                if e <= 0 or c <= 0 or sz <= 0:
                    continue
                d = 1 if str(r["side"]).lower() == "long" else -1
                gross += (c - e) * d * sz + float(r["partial_realized_pnl"] or 0)
                fees += float(r["fee"] or 0)
                n += 1
            if n == 0:
                print(f"  {sym}: 近 14 天无已平仓记录")
                continue
            net = gross - fees
            share = abs(fees) / max(abs(gross), 1e-9)
            print(f"  {sym}: n={n:<3} 现状(毛)={gross:+9.2f}   预览(净)={net:+9.2f}   "
                  f"费用={fees:6.2f}（占毛 {share:.1%}）")
        print("\n  说明：现状公式 `(close-entry)*dir*size + partial_realized_pnl` 不含费用；")
        print("        预览版仅**减去 `partial_fee_paid`**，样本与其它字段不变。")
    finally:
        db.close()


# ───────────────────── §35 factor_route 呈现 ─────────────────────

def p35() -> None:
    sec("§35 `factor_route` 呈现（票缺样本量 / confidence 名不副实）")
    price = None
    try:
        from backend.services.analysis.context_pack import _klines
        kl = _klines("BTC", "4h", 5)
        if kl:
            price = float(kl[-1].get("close") or 0) or None
    except Exception:
        pass
    if not price:
        print("  取不到参考价，跳过")
        return
    from backend.services.factor_engine.midlong_factor_route import factor_route_decide
    out = factor_route_decide("BTC", market_summary={"BTC": {"price": price, "last": price}}) or {}
    votes = out.get("votes") or {}
    print(f"  现状：action={out.get('action')} score={out.get('score')} "
          f"confidence={out.get('confidence')}（= clip(50+|score|*30)）  票数={len(votes)}")
    # 上游样本量
    ns: dict = {}
    try:
        w = json.loads((ROOT / "data/factor_runtime_weights.json").read_text(encoding="utf-8"))
        for k, v in (w.get("stats") or {}).items():
            if isinstance(v, dict) and v.get("n"):
                ns[str(k)] = int(v["n"])
    except Exception:
        pass
    print(f"  上游带样本量的因子数={len(ns)}")
    print("\n  --- 现状票（前 3）vs 预览票（加 n）---")
    for k, v in list(votes.items())[:3]:
        base = k.split("@")[0]
        n = ns.get(k) or ns.get(base)
        n_txt = f'"n": {n}' if n else '"n": null  ⚠上游无样本量'
        print(f"    {k}")
        print(f"      现状: {json.dumps(v, ensure_ascii=False, default=str)}")
        print(f"      预览: {{{json.dumps(v, ensure_ascii=False, default=str)[1:-1]}, {n_txt}}}")
    print("\n  另建议：`confidence` 改名 `score_scaled` 或注明『= |score| 线性映射，非置信度』；")
    print("           `reason` 里的 16 票裸数字（与 votes 重复）可删，减小载荷噪声。")


def main() -> int:
    print("读数诚实性（§32–§35）改动前/后对照预览 —— 只读、不落盘、不生效")
    for fn in (p32, p33, p34, p35):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"  [{fn.__name__}] 预览失败: {type(exc).__name__}: {str(exc)[:120]}")
    print("\n" + "=" * 92)
    print("以上预览均**未改动任何生产文件/配置**；采纳与否由你决定（待办 B18）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
