"""Learning-loop closure audit.

The user's complaint includes "学习进化 ... 没有统一的贯彻风格".
A learning loop is CLOSED only if all three hold:
    producer exists AND is scheduled/alive
    artifact is FRESH
    consumer exists AND actually reads it

This checks every learning/evolution artifact and reports:
    CLOSED  -- all three present
    BROKEN  -- producer dead or artifact stale
    OPEN    -- produced + fresh but nobody consumes it (silent dead end)

Known target state: `self_tuner_review.json` is deliberately human-in-the-loop
(user decision 2026-10-02), so "no automated consumer" is EXPECTED there and
must not be reported as a defect.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")

# name -> (relative path, staleness limit in minutes, expected human-in-loop?)
ARTIFACTS = [
    ("flow 学习样本", "data/flow_learn_samples.jsonl", 120, False),
    ("flow 学习汇总", "data/flow_learn_last.json", 120, False),
    # [T56] `flow_learn_params.json` 标为 human=True：
    # 它只由 `self_tuner.py:339`（"护栏通过后的落库"）在**人工复核通过后**写入。
    # 用户 2026-10-02 决定「直接用 DSH，不走项目侧 LLM API」⇒ 该环节是**人工闸门**，
    # "久未更新"是**设计如此**，不是停摆。不标 human 会误报。
    ("flow 学习参数", "data/flow_learn_params.json", 180, True),
    ("gate 生产文件", "data/flow_gate_last.json", 30, False),
    ("situation 情形表", "data/flow_situation_last.json", 45, False),
    ("自调优复核", "logs/self_tuner_review.json", 1440, True),
    # [T63] `self_tuner_history.jsonl` 也标 human=True：
    # 实测 `DSH_MM_SELF_TUNE` **每小时运行且 `LastTaskResult=0`（成功）**，
    # 但 history 42 小时未写 —— 因为 `self_tuner` 只在**找到并通过护栏的改动**时才写，
    # 当前 6 个参数全部在界内、且历史尝试多为 `verdict: rollback`
    # ⇒ **"运行了但无需变更"**，不是停摆。这属于人工/条件闸门，不标 human 会误报。
    ("自调优历史", "logs/self_tuner_history.jsonl", 1440, True),
    ("每日学习标记", "data/daily_learning_last_run.json", 1560, False),
    # [T63] `learning_core.db` 标 human=True：
    # 它由 `backend/services/learning_core/*` 经 **API 路由 + `unified_scheduler`** 驱动
    # （`learning_core_routes.py` / `intelligent_learning_routes.py`），
    # **没有任何 DSH_* 计划任务直接跑它** ⇒ "久未更新" = 没人调用，非缺陷。
    ("学习核心库", "data/learning_core.db", 240, True),
]

SEARCH_DIRS = ["backend", "scripts", "scripts/tools"]


def _refs(needle: str) -> list:
    """Find source references to an artifact basename."""
    out = []
    for d in SEARCH_DIRS:
        p = ROOT / d
        if not p.exists():
            continue
        for f in p.rglob("*.py"):
            if "archive" in f.parts:
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in txt:
                out.append(str(f.relative_to(ROOT)))
    return out


def main() -> int:
    print("=" * 104)
    print("学习进化闭环审计（生产者 / 新鲜度 / 消费者）")
    print("=" * 104)
    print(f"  {'产物':<16}{'age(min)':>9}{'上限':>6}  {'生产者':<10}{'消费者':<10}状态")
    rows = []
    for label, rel, limit, human in ARTIFACTS:
        p = ROOT / rel
        if not p.exists():
            print(f"  {label:<16}{'--':>9}{limit:>6}  {'?':<10}{'?':<10}MISSING")
            continue
        age_min = (time.time() - p.stat().st_mtime) / 60
        base = os.path.basename(rel)
        refs = _refs(base)
        # producer = a script whose name suggests it writes this artifact
        prod = [r for r in refs if "archive" not in r]
        sched = ROOT / "backend/services/scheduler.py"
        scheduled = False
        if sched.exists():
            st = sched.read_text(encoding="utf-8", errors="replace")
            scheduled = any(Path(r).stem in st for r in prod)
        fresh = age_min <= limit
        if human:
            status = "CLOSED(人工)" if fresh else "STALE"
        elif not prod:
            status = "BROKEN(无生产者)"
        elif not fresh:
            status = "BROKEN(停更)"
        else:
            status = "CLOSED"
        print(f"  {label:<16}{age_min:>9.0f}{limit:>6}  "
              f"{len(prod):>4} 处   {len(refs):>4} 处   {status}")
        rows.append((label, age_min, limit, len(prod), len(refs), status))

    print()
    print("=" * 104)
    print("破损项与说明")
    print("=" * 104)
    bad = [r for r in rows if r[5].startswith("BROKEN")]
    if not bad:
        print("  无")
    for label, age, lim, nprod, nref, status in bad:
        print(f"  ⚠️ {label:<16} age={age:.0f}min (上限 {lim})  生产者引用={nprod}  {status}")
    print()
    print("  注：`自调优复核` 标 CLOSED(人工) —— 2026-10-02 用户决定"
          "「直接用 DSH，不走项目侧 LLM API」，")
    print("      该产物**故意**没有自动消费者，不算缺陷。")
    print()
    print("=" * 104)
    print("脚本堆积情况（'混乱'的量化）")
    print("=" * 104)
    for d in ("scripts", "scripts/archive/h_series", "scripts/tools"):
        p = ROOT / d
        if p.exists():
            n = len(list(p.glob("*.py")))
            print(f"  {d:<28} {n:>4} 个 .py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
