# -*- coding: utf-8 -*-
"""[§87 审计 2026-09-11 / 决策 P28-A] long 层"止损链"只读审计：**为什么 0/14 触发**？

#71 的事实：long 层 30 天 34 笔离场里 `sl` 通道 = 0 笔，而亏损单平均 −$19.22（mid 的 6.3×）。
本脚本用**持仓期最大不利偏移** `trough_pnl_pct` 与入场 SL 距离做逐笔比对，给出四种结论：

  ① **未触及**：`trough > −SL%` ⇒ 价格根本没到止损位 ⇒ 止损"没机会"触发（真正的离场者是叙事通道）；
  ② **触及未执行**：`trough ≤ −SL%` 但 `close_reason != sl` ⇒ **止损该响没响**（执行链缺陷）；
  ③ **超出**：`trough` 明显深于 `−SL%`（超出 >0.5pt）⇒ 同理，且亏损被放大；
  ④ 正常：`close_reason == sl`。

同时对 mid 层做同样统计当**对照**（mid 有 5 笔走 sl），并扫源码确认"谁负责发 sl 离场"
（`close_reason="sl"` 的生产者）以及是否覆盖 long 层。
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")
BUFFER_PT = 0.5          # 认为"超出 SL"需要比 SL 位再深 0.5pt（容滑点/插针）


def classify(trough_pct: float, sl_dist_pct: float, closed_via_sl: bool) -> str:
    """纯函数（可测）：判定该笔的止损触发情况。

    ⚠️ **单位契约**（第一版就在这里翻车）：两个入参都必须是**百分数**
    （`trough_pct` 例 −1.8 表示 −1.8%，不是分数 −0.018；`sl_dist_pct` 例 6.52）。
    DB 里的 `trough_pnl_pct`/`peak_pnl_pct` 是**分数**，调用方必须先 ×100。
    """
    if closed_via_sl:
        return "正常(sl)"
    if sl_dist_pct <= 0:
        return "无SL"
    if trough_pct > -sl_dist_pct:
        return "未触及"
    if trough_pct <= -(sl_dist_pct + BUFFER_PT):
        return "超出SL未执行"
    return "触及未执行"


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    since = sys.argv[2] if len(sys.argv) > 2 else None      # 例：2026-08-31（MAE 遥测起始）
    where_time = (f"closed_at >= timestamp '{since}'" if since
                  else f"closed_at >= now() - interval '{days} day'")
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select upper(symbol), lower(coalesce(timeframe_tier,'?')), coalesce(close_reason,'?'),
                   {NET} as net, entry_price, sl_price, coalesce(trough_pnl_pct,0),
                   coalesce(peak_pnl_pct,0)
            from paper_positions
            where status='closed' and {where_time}
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')
              and entry_price is not null and entry_price > 0
        """)).fetchall()
    finally:
        db.close()

    items = []
    for r in rows:
        entry = float(r[4] or 0)
        sl = float(r[5]) if r[5] is not None else None
        sl_pct = (abs(entry - sl) / entry * 100.0) if (sl and entry > 0) else 0.0
        ch = channel_of(r[2])
        # [单位契约] DB 里 trough/peak 是**分数**（−0.018 = −1.8%）⇒ 统一换成百分数再比较
        trough_pct = float(r[6] or 0) * 100.0
        peak_pct = float(r[7] or 0) * 100.0
        items.append({"sym": r[0], "tier": r[1], "channel": ch, "net": float(r[3] or 0),
                      "entry": entry, "sl_pct": sl_pct, "trough": trough_pct,
                      "peak": peak_pct,
                      "verdict": classify(trough_pct, sl_pct, ch == "sl")})

    print("=" * 100)
    window = f"自 {since}" if since else f"近 {days} 天"
    print(f"long 层止损链审计（{window} mid/long，n={len(items)}；超出阈值 +{BUFFER_PT}pt）")
    print("=" * 100)
    # ⚠️ 前置守卫：MAE 遥测（trough_pnl_pct）只在 2026-08-31 之后被维护
    #    ⇒ 窗口里若大量 trough=0，那不是"没触及止损"，而是"没有遥测"。必须先看覆盖率！
    print("\nQ0. **遥测覆盖率守卫**（trough_pnl_pct 非零比例；2026-08-31 起才被维护）")
    for tier in ("mid", "long"):
        xs = [x for x in items if x["tier"] == tier]
        if not xs:
            continue
        nz = sum(1 for x in xs if x["trough"] != 0)
        print(f"  {tier:5s} n={len(xs):>3}｜trough 非零 {nz:>3}（{100.0*nz/len(xs):>5.1f}%）"
              + ("  ⚠️ 覆盖率不足 ⇒ 下面的『未触及』结论无效" if nz / max(1, len(xs)) < 0.8 else "  ✅ 可判定"))
    print("\nQ1. 止损位与最大不利偏移（逐层）")
    print(f"  {'层':5s} {'笔数':>4} {'SL覆盖':>7} {'SL距离均值':>10} {'trough均值':>10} "
          f"{'trough中位':>10} {'trough深于-SL的笔数':>18}")
    for tier in ("mid", "long"):
        xs = [x for x in items if x["tier"] == tier]
        if not xs:
            continue
        srt = sorted(x["trough"] for x in xs)
        deeper = sum(1 for x in xs if x["sl_pct"] > 0 and x["trough"] <= -x["sl_pct"])
        print(f"  {tier:5s} {len(xs):>4} {sum(1 for x in xs if x['sl_pct']>0):>4}/{len(xs):<3}"
              f"{(sum(x['sl_pct'] for x in xs)/len(xs)):>9.2f}% "
              f"{(sum(x['trough'] for x in xs)/len(xs)):>9.2f}% "
              f"{srt[len(srt)//2]:>9.2f}% {deeper:>18}")

    print("\nQ2. 触发判定（四分类）")
    print(f"  {'层':5s} " + " ".join(f"{k:>14s}" for k in ("正常(sl)", "未触及", "触及未执行", "超出SL未执行", "无SL")))
    for tier in ("mid", "long"):
        c = defaultdict(int)
        for x in items:
            if x["tier"] == tier:
                c[x["verdict"]] += 1
        print(f"  {tier:5s} " + " ".join(f"{c.get(k,0):>14d}" for k in
                                         ("正常(sl)", "未触及", "触及未执行", "超出SL未执行", "无SL")))

    print("\nQ3. **触而未执行 / 超出** 的样本明细（这才是执行链缺陷的直接证据）")
    bad = [x for x in items if x["verdict"] in ("触及未执行", "超出SL未执行")]
    if not bad:
        print("  （无）—— 说明止损位从未被触及，0/14 属『没机会触发』而非『该响没响』")
    for x in sorted(bad, key=lambda y: y["net"])[:14]:
        print(f"  {x['tier']:4s} {x['sym']:10s} {x['channel']:22s} trough={x['trough']:>7.2f}% "
              f"SL={x['sl_pct']:>5.2f}% peak={x['peak']:>5.2f}% 净额={x['net']:>8.2f}  ← {x['verdict']}")

    print("\nQ4. 亏损单里『接近止损』的程度（trough 距离 −SL 还差多少）")
    for tier in ("mid", "long"):
        ls = [x for x in items if x["tier"] == tier and x["net"] < 0 and x["sl_pct"] > 0
              and x["verdict"] != "正常(sl)"]
        if not ls:
            continue
        gaps = sorted(x["trough"] + x["sl_pct"] for x in ls)      # >0 = 未到止损位
        near = sum(1 for g in gaps if g < 1.0)
        print(f"  {tier:5s} 亏损 {len(ls):>3} 笔｜距止损位：最近 {gaps[0]:>6.2f}pt｜中位 "
              f"{gaps[len(gaps)//2]:>6.2f}pt｜<1pt 内 {near:>2} 笔（{100.0*near/len(gaps):>4.1f}%）")

    print("\nQ5. 源码：谁负责发 `sl` 离场？（确认 long 层是否被覆盖）")
    pat = re.compile(r"close_reason\s*=\s*[\"']sl[\"']|reason\s*=\s*[\"']sl[\"']|[\"']sl[\"']\s*:")
    hits = []
    for p in (ROOT / "backend").rglob("*.py"):
        if any(part in p.parts for part in (".venv", "__pycache__", "tests")) or "_archive" in p.parts:
            continue
        try:
            src = p.read_bytes().decode("utf-8-sig", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            if pat.search(line):
                hits.append((p.relative_to(ROOT), i, line.strip()[:110]))
    for rel, i, line in hits[:12]:
        print(f"  {rel}:{i}  {line}")
    print(f"  ⇒ 共 {len(hits)} 处候选（需人工确认哪些覆盖 long 层）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
