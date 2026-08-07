#!/usr/bin/env python3
"""中线(swing)/长线(trend_follow) 决策审计脚本。

2026-07-06 新增：用于回答"中线/长线分析是否达到要求、有无数据缺失、幻觉是否过大"。

背景：SwingAgent/TrendAgent 的真实 LLM 分析在独立调度循环中每 tick 都会执行，但此前
"hold"（无信号，占绝大多数）结果只写入会话事件流，不落库到 ai_decision_logs，导致数据库
里长期停留着 Fix18 调度桩占位文案，看起来像"分析没有真正跑"。已在
full_auto_trading_service.py 增加 `_persist_independent_scan_log`，把每次真实分析
（含 hold）连同 cited_fact_ids / evidence_checklist / fact_guard 一并落库。

本脚本统计最近 N 小时内：
  1. stub（调度桩占位，未真正分析）vs real（真实 LLM 分析）比例
  2. evidence_checklist 平均可用率（是否有数据缺失）
  3. cited_fact_ids 覆盖率（LLM 是否按要求引用证据）
  4. FactGuard 违规次数（分析结果与证据矛盾 = 疑似幻觉）
  5. cycle_prob_* 事实的可用率（新增的周期方向概率引擎证据是否真正生效）

用法：
    python scripts/audit_midlong_evidence.py --hours 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import psycopg  # noqa: E402

STUB_STRINGS = {
    "[中长线AI强制→SwingAgent LLM]",
    "[总控独立调度→SwingAgent待分析]",
    "[中长线AI强制→TrendAgent LLM]",
    "[总控独立调度→TrendAgent待分析]",
}


def _dsn() -> str:
    raw = os.environ.get(
        "ANALYTICS_DATABASE_URL", "postgresql://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_analytics"
    )
    return raw.replace("postgresql+psycopg://", "postgresql://")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    args = ap.parse_args()

    conn = psycopg.connect(_dsn())
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, decision_time, operation, symbol, decision_snapshot
        FROM ai_decision_logs
        WHERE decision_time > now() - (%s || ' hours')::interval
        ORDER BY decision_time DESC
        """,
        (str(args.hours),),
    )
    rows = cur.fetchall()
    print(f"近 {args.hours} 小时 ai_decision_logs 总行数: {len(rows)}")

    stats = Counter()
    cited_stats = Counter()
    evidence_avail = {"swing": [], "trend_follow": []}
    cycle_prob_avail = {"swing": [0, 0], "trend_follow": [0, 0]}  # [available, total_seen]
    fg_violation_counter = Counter()

    for _id, dt, op, sym, snap in rows:
        if not snap:
            continue
        try:
            d = json.loads(snap)
        except Exception:
            continue
        nat = d.get("trade_nature")
        if nat not in ("swing", "trend_follow"):
            continue
        reasoning = d.get("reasoning") or ""
        is_stub = reasoning in STUB_STRINGS or bool(d.get("_orch_scheduled"))
        stats[f"{nat}|{'stub(占位未分析)' if is_stub else 'real(真实分析)'}"] += 1

        has_agent_source = bool(d.get("agent_source"))
        cited = d.get("cited_fact_ids")
        has_cited = bool(cited)
        cited_stats[f"{nat}|agent_source={'Y' if has_agent_source else 'N'}|cited={'Y' if has_cited else 'N'}"] += 1

        ev = d.get("agent_evidence") or {}
        checklist = ev.get("evidence_checklist") if isinstance(ev, dict) else None
        fg = ev.get("fact_guard") if isinstance(ev, dict) else None
        if checklist:
            total = len(checklist)
            avail = sum(1 for f in checklist if f.get("available"))
            evidence_avail[nat].append((avail, total))
            cp_facts = [f for f in checklist if str(f.get("id", "")).startswith("cycle_prob_dir_")]
            if cp_facts:
                cycle_prob_avail[nat][1] += len(cp_facts)
                cycle_prob_avail[nat][0] += sum(1 for f in cp_facts if f.get("available"))
        if fg and fg.get("violations"):
            for v in fg["violations"]:
                fg_violation_counter[f"{nat}|{v}"] += 1

    print("\n== 1. stub(占位未分析) vs real(真实分析) ==")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")

    print("\n== 2. agent_source / cited_fact_ids 覆盖率 ==")
    for k, v in sorted(cited_stats.items()):
        print(f"  {k}: {v}")

    print("\n== 3. evidence_checklist 可用率（有无数据缺失） ==")
    for nat, lst in evidence_avail.items():
        if lst:
            tot_avail = sum(a for a, t in lst)
            tot_all = sum(t for a, t in lst)
            print(f"  {nat}: 决策数={len(lst)} 平均可用度={tot_avail}/{tot_all} = {tot_avail/tot_all:.1%}")
        else:
            print(f"  {nat}: 0 条决策带 evidence_checklist（无法审计）")

    print("\n== 4. cycle_prob_* 周期方向概率引擎事实可用率 ==")
    for nat, (avail, total) in cycle_prob_avail.items():
        if total:
            print(f"  {nat}: {avail}/{total} = {avail/total:.1%}")
        else:
            print(f"  {nat}: 无样本")

    print("\n== 5. FactGuard 违规（分析结果与证据矛盾 = 疑似幻觉） ==")
    if fg_violation_counter:
        for k, v in fg_violation_counter.most_common(20):
            print(f"  {k}: {v}")
    else:
        print("  无违规记录")


if __name__ == "__main__":
    main()
