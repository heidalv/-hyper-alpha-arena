# -*- coding: utf-8 -*-
"""[2026-10-03] 学习进化链路"全面补齐"护栏 —— 驱动恢复 + L3 裁决 + 可观测性。

## 实证（本次补齐的依据）
· `data/hermes_evolution.db.task_run_log`：23 个 `hermes_*` / `opencode_*` 任务最后一次运行
  **全部停在 2026-08-16**；`hermes_routes.py` 注释写着「[2026-08-17] opencode_scheduler 已删除」
  —— 驱动被删后**没有任何替代**，学习链路虽然接口可读，却再无任何一环自动推进。
· 触发验证（补齐后）：`POST /api/hermes/run/wisdom_accumulate` → 智慧记录 **232 → 243（+11）**，
  证明 L1 累积链路此前确实冻结、现已复活。
· L3：`POST /api/hermes/architecture/967/accept` → `{"status":"implemented"}`，pending **286→285**、
  implemented **681→682** ⇒ **accepted/rejected 计数恒为 0 是状态词汇设计，不是"无人裁决"**
  （这条纠正已同步进 ChainOverviewPanel 的判据）。
· L4：`genesis_check` 可运行但 `checked={validated:0,rejected:0}` —— 345 个孵化候选 `paper_trades=0`，
  因为校验门槛要求 paper 交易 ≥30 笔/胜率≥45%/均盈≥$1/≥3 天，**L4 依赖纸面交易在跑**（设计依赖）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_SCHED = (ROOT / "backend/services/scheduler.py").read_text(encoding="utf-8")
SRC_STARTUP = (ROOT / "backend/services/startup.py").read_text(encoding="utf-8")
SRC_ROUTES = (ROOT / "backend/api/hermes_routes.py").read_text(encoding="utf-8")
SRC_PANEL = (ROOT / "frontend-next/src/components/learning/HermesLevelsPanel.tsx").read_text(encoding="utf-8")
SRC_CHAIN = (ROOT / "frontend-next/src/components/learning/ChainOverviewPanel.tsx").read_text(encoding="utf-8")


# ─────────────── 一、驱动恢复 ───────────────

def test_scheduler_restores_deterministic_levels_by_default():
    assert "def start_hermes_scheduler()" in SRC_SCHED
    seg = SRC_SCHED.split("def start_hermes_scheduler()")[1].split("def start_asset_curve_broadcast")[0]
    # L1/L4 默认注册（确定性任务）
    assert '("hermes_wisdom_accumulate", "accumulate_wisdom", 900)' in seg
    assert '("hermes_genesis_check", "check_genesis_candidates", 3600)' in seg
    # L2/L3（LLM 改写类，会影响交易行为）必须默认关闭
    assert 'HERMES_L3_ENABLED", "false"' in seg, "L3 必须默认关"
    assert 'HERMES_L2_ENABLED", "false"' in seg, "L2 必须默认关"
    assert 'HERMES_SCHEDULER_ENABLED", "true"' in seg, "总开关默认开（回滚用）"


def test_hermes_scheduler_call_survives_standalone_data_center():
    """第一次修复踩过的坑：把调用放进 `else:`（DATA_CENTER_MODE 分支）里会被整段跳过。
    实测本机 DATA_CENTER_MODE=standalone ⇒ live_jobs 为空、任务根本没注册。"""
    call = SRC_STARTUP.index("start_hermes_scheduler()")
    branch = SRC_STARTUP.index("if _dc_external:")
    assert call < branch, "start_hermes_scheduler() 必须在 _dc_external 分支之外调用"


def test_task_run_log_is_written_by_ticks():
    seg = SRC_SCHED.split("def start_hermes_scheduler()")[1].split("def start_asset_curve_broadcast")[0]
    assert "increment_task_run_count(job_id)" in seg, "tick 必须累计 run_count"
    assert "UPDATE task_run_log SET last_status=?" in seg, "tick 必须写回状态/耗时/错误"


# ─────────────── 二、坏接口修复 + 可观测性 ───────────────

def test_schedule_route_import_bug_fixed_and_task_log_exposed():
    seg = SRC_ROUTES.split("def hermes_schedule()")[1].split("_HERMES_RUN_HANDLERS")[0]
    assert "from backend.services.hermes_orchestrator import hermes as _orch" in seg, \
        "必须导入真实单例 `hermes`（此前导入不存在的 `hermes_orchestrator` ⇒ 接口永远返回 error）"
    # 只测"代码行"，不看注释里对旧 bug 的描述
    bad = [ln for ln in seg.splitlines()
           if ln.strip().startswith("from backend.services.hermes_orchestrator import hermes_orchestrator")]
    assert not bad, "旧的错误导入不得回归"
    assert "task_run_log" in seg and "live_jobs" in seg
    assert '@router.get("/task-log")' in SRC_ROUTES, "task_run_log 必须暴露给前端（否则停摆不可见）"


# ─────────────── 三、前端：L3 裁决 + 驱动横幅 ───────────────

def test_panel_wires_l3_review_endpoints():
    for ep in ("/architecture/${id}/${action}", "auto-accept-pending", "reconcile-implemented"):
        assert ep in SRC_PANEL, f"L3 裁决未接: {ep}"
    assert 'data-testid={`accept-${p.id}`}' in SRC_PANEL and 'data-testid={`reject-${p.id}`}' in SRC_PANEL
    assert "architecture?status=pending" in SRC_PANEL, "需拉取 pending 列表才能裁决"
    assert "task-log" in SRC_PANEL and "驱动在跑" in SRC_PANEL and "无驱动注册" in SRC_PANEL
    assert "L4 依赖纸面交易在跑" in SRC_PANEL, "L4 的设计依赖必须写清楚"


def test_chain_panel_no_longer_misjudges_l3_accepted_counter():
    """自我纠正的护栏：accept 直接落 implemented，accepted 计数恒为 0 不能当作断链证据。"""
    assert "num(l3.accepted) + num(l3.rejected) + num(l3.implemented)" in SRC_CHAIN
    assert "l3PendingPct" in SRC_CHAIN
    assert "accepted 计数恒为 0" in SRC_CHAIN
