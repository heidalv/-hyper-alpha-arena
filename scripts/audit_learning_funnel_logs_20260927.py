# -*- coding: utf-8 -*-
"""[新目标 R4] 学习漏斗的**日志侧**时间感知计数。

为什么必须走日志：`ai_strategies.learning_enabled` 只有**当前值**，用它统计历史平仓会重演
§97 的"状态漂移/前视偏差"错误（六个 tpl_mid_range_* 模板是 2026-09-27 06:45 批量毕业的，
在此之前的平仓**当时并没有被闸门挡住**）。日志行是平仓当刻写下的，且每次命中都有一行 INFO，
所以它是唯一无偏的"当时是否被挡"证据。

统计口径（全部按行首时间戳自然日）：
  · gate_off        `learning_enabled=false`           → 整段学习被跳过（含 MLTO postmortem）
  · no_thesis       `mlto_block_skip=no_thesis`        → MLTO 块因缺 thesis_id 未进入
  · pm_queued       `MLTO postmortem queued`           → postmortem 事件成功入队
  · dup_skip        `重复 outcome 跳过学习`            → 记账去重短路
  · pm_worker_err   `thesis postmortem skip`           → 异步 worker 落库异常（DEBUG 级）
  · fallback_hit    `thesis_id 兜底解析`               → R4 兜底解析命中
用法：.venv\\Scripts\\python.exe scripts\\audit_learning_funnel_logs_20260927.py [起始日 YYYY-MM-DD]
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs"

PATTERNS = {
    "gate_off": re.compile("learning_enabled=false"),
    "no_thesis": re.compile("mlto_block_skip=no_thesis"),
    "pm_queued": re.compile(r"\[LearningBus\] MLTO postmortem queued"),
    "dup_skip": re.compile("重复 outcome 跳过学习"),
    "pm_worker_err": re.compile("thesis postmortem skip"),
    "fallback_hit": re.compile("thesis_id 兜底解析"),
    "pm_event_ok": re.compile(r"\[MLTO learning\]\[trace\] done"),
}
TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) ")


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-09-15"
    files = sorted(LOGS.glob("backend*.log"))
    per_day = defaultdict(Counter)
    per_day_strategy = defaultdict(Counter)
    totals = Counter()
    scanned = 0
    for p in files:
        try:
            if datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d") < since:
                continue
        except Exception:
            continue
        scanned += 1
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    for name, rx in PATTERNS.items():
                        if rx.search(line):
                            totals[name] += 1
                            m = TS.match(line)
                            day = m.group(1) if m else "?"
                            if day >= since:
                                per_day[day][name] += 1
                                if name == "gate_off":
                                    sid = re.search(r"策略 (\S+) learning_enabled", line)
                                    if sid:
                                        per_day_strategy[day][sid.group(1)] += 1
        except Exception as exc:
            print(f"[warn] 读取失败 {p.name}: {exc}")

    print("=" * 96)
    print(f"日志侧漏斗（{since} 起，扫描 {scanned}/{len(files)} 个 backend 日志文件）")
    print("=" * 96)
    head = f"{'日期':<12}{'gate_off':>9}{'no_thesis':>10}{'pm_queued':>10}{'dup_skip':>9}{'worker_err':>11}{'fallback':>9}{'pm_event':>9}"
    print(head)
    for day in sorted(per_day):
        c = per_day[day]
        print(f"{day:<12}{c['gate_off']:>9}{c['no_thesis']:>10}{c['pm_queued']:>10}"
              f"{c['dup_skip']:>9}{c['pm_worker_err']:>11}{c['fallback_hit']:>9}{c['pm_event_ok']:>9}")
    print("-" * 96)
    print(f"{'合计':<12}{totals['gate_off']:>9}{totals['no_thesis']:>10}{totals['pm_queued']:>10}"
          f"{totals['dup_skip']:>9}{totals['pm_worker_err']:>11}{totals['fallback_hit']:>9}{totals['pm_event_ok']:>9}")
    print()
    print("被闸门挡住的策略 Top（按天聚合，节选每天前 4）：")
    allstr = Counter()
    for day in sorted(per_day_strategy):
        allstr.update(per_day_strategy[day])
    for sid, n in allstr.most_common(12):
        days = [d for d in sorted(per_day_strategy) if per_day_strategy[d][sid]]
        print(f"   {sid:<26} n={n:<4} 首见={days[0] if days else '?'} 末见={days[-1] if days else '?'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
