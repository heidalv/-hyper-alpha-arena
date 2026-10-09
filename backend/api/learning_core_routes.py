"""统一进化学习内核 — 对外 API（/api/learning/*）

合并"策略假设引擎 / Hermes / 智能学习中心"后的统一入口。

  GET  /api/learning/overview          统一进化中枢概览（取代两套重叠 overview）
  GET  /api/learning/lineage           按 lineage_id 回放完整血缘链路
  GET  /api/learning/events            最近血缘事件（可按 stage 过滤）
  GET  /api/learning/lineages          最近血缘链路摘要（实时管线首屏）
  GET  /api/learning/flags             读取内核特性开关
  POST /api/learning/flags             运行时修改内核特性开关（内存，重启失效）
  POST /api/learning/hypothesis/run    触发假设全周期（生成→验证→晋升，写血缘）
  POST /api/learning/hermes/run/{task} 触发一次 Hermes 任务
  GET  /api/learning/status            内核 + 账本健康状态

注：/api/learning/loop 与 /api/learning/dashboard 为既有子路由，本路由不与之冲突。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["LearningCore"])


class FlagUpdate(BaseModel):
    key: str
    value: bool


class HypothesisRunBody(BaseModel):
    symbols: Optional[List[str]] = None
    regime: Optional[str] = None


@router.get("/overview")
def get_overview():
    """统一进化中枢概览。"""
    from backend.services.learning_core import orchestrator
    return orchestrator.overview()


@router.get("/lineage")
def get_lineage(lineage_id: str = Query(..., description="血缘链路 ID")):
    """按 lineage_id 回放完整血缘链路（从假设到部署）。"""
    from backend.services.learning_core import orchestrator
    events = orchestrator.get_lineage(lineage_id)
    return {"lineage_id": lineage_id, "count": len(events), "events": events}


@router.get("/events")
def get_events(
    limit: int = Query(100, ge=1, le=1000),
    stage: Optional[str] = Query(None, description="按阶段过滤"),
):
    """最近血缘事件。"""
    from backend.services.learning_core import orchestrator
    return {"events": orchestrator.recent_events(limit=limit, stage=stage)}


@router.get("/lineages")
def get_lineages(limit: int = Query(30, ge=1, le=200)):
    """最近血缘链路摘要（实时管线首屏）。"""
    from backend.services.learning_core import orchestrator
    return {"lineages": orchestrator.recent_lineages(limit=limit)}


@router.get("/flags")
def get_flags():
    """读取内核特性开关。"""
    from backend.services.learning_core import flags
    return {"flags": flags.all_flags(), "keys": flags.flag_keys()}


@router.post("/flags")
def update_flag(body: FlagUpdate):
    """运行时修改内核特性开关（仅内存，重启后恢复默认/env）。"""
    from backend.services.learning_core import flags
    try:
        flags.set_flag(body.key, body.value)
        return {"key": body.key, "value": flags.get_flag(body.key), "status": "updated"}
    except KeyError as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/hypothesis/run")
def run_hypothesis(body: HypothesisRunBody):
    """触发一次假设全周期（生成→验证→晋升），并写入血缘账本。"""
    from backend.services.learning_core import orchestrator
    ctx = {"regime": body.regime or "unknown", "source": "api_manual"}
    summary = orchestrator.run_hypothesis_cycle(symbols=body.symbols, market_context=ctx)
    return {"summary": summary}


@router.post("/hermes/run/{task}")
def run_hermes(task: str):
    """触发一次 Hermes 任务（wisdom / meta / prompt / architecture / genesis）。"""
    from backend.services.learning_core import orchestrator
    return orchestrator.run_hermes_task(task)


class BacktestIngestBody(BaseModel):
    source: str
    symbol: Optional[str] = None
    metrics: dict
    template_id: Optional[str] = None
    run_id: Optional[str] = None
    lineage_id: Optional[str] = None
    trades: Optional[List[dict]] = None


@router.post("/backtest/ingest")
def ingest_backtest(body: BacktestIngestBody):
    """吸收一次回测结果：统一记血缘 + 驱动参数优化 + 写 RL replay buffer。"""
    from backend.services.learning_core.backtest_loop import backtest_loop
    return backtest_loop.ingest_result(
        source=body.source,
        symbol=body.symbol,
        metrics=body.metrics,
        template_id=body.template_id,
        run_id=body.run_id,
        lineage_id=body.lineage_id,
        trades=body.trades,
    )


@router.get("/replay/stats")
def replay_stats():
    """RL 经验回放缓冲区统计。"""
    from backend.services.learning_core.rl_core.replay_buffer import replay_buffer
    return replay_buffer.stats()


@router.post("/replay/seed-real")
def replay_seed_real(days: int = 180, limit: int = 1000):
    """[2026-10-03 ③] 用**真实已平仓决策**补齐 RL 回放缓冲（去合成化）。

    实测缓冲长期是 300/302 synthetic（真实仅 2 条）⇒ RL 几乎全在合成数据上训练。
    本接口把 `decision_snapshots` 里 executed=true 且已回填 pnl 的真实成交转成 transition
    （source=live、reward=真实 pnl_pct），可重复调用（按 symbol+action+reward 近似去重）。
    """
    from backend.services.learning_core.replay_seed import seed_from_real_decisions
    return seed_from_real_decisions(days=days, limit=limit)


@router.post("/backfill-decision-pnl")
def backfill_decision_pnl_route(days: int = 90, limit: int = 2000, dry_run: bool = False):
    """[2026-10-03] 把已平仓持仓的真实盈亏**回填到对应决策快照**（决策↔成交回填率修复）。

    实测背景：`executed=229` 条快照里只有 17 条有 pnl（7.4%），回放缓冲因此长期缺真实样本。
    根因两条：①主库 RLS 未注入 ⇒ 只看到 115/403 笔持仓；②匹配窗口是 `now-48h` 且候选歧义即跳过
    ⇒ 引擎侧改为**以 opened_at 为中心的开仓窗口**；本接口用**两段窗口（±30min → ±6h，均要求唯一匹配）**
    补齐历史缺口。实测 17 → **47**（20.5%），ambiguous=0（无错配）。
    仍然匹配不上的主要是**持仓已被「完整重置」删除**（盈亏已不存在，不猜）。
    """
    from backend.services.decision_pnl_backfill import backfill_decision_pnl
    return backfill_decision_pnl(days=days, limit=limit, dry_run=dry_run)


# ══════════════════════════════════════════════════════════════
#  RL 交易决策 agent（影子先行）
# ══════════════════════════════════════════════════════════════

class RLTrainBody(BaseModel):
    batch_size: int = 256
    epochs: int = 5


class RLDecideBody(BaseModel):
    symbol: str
    timeframe: str = "1h"
    position: float = 0.0


@router.get("/rl/status")
def rl_status():
    """RL agent 状态（开关 / 策略 / 影子门控）。"""
    from backend.services.learning_core.rl_core.shadow import shadow_service
    from backend.services.learning_core.rl_core.replay_buffer import replay_buffer
    return {"shadow": shadow_service.status(), "replay": replay_buffer.stats()}


@router.post("/rl/train")
def rl_train(body: RLTrainBody):
    """从 ReplayBuffer 离线训练 RL 策略。"""
    from backend.services.learning_core.rl_core.policy import policy
    return policy.train_from_replay(batch_size=body.batch_size, epochs=body.epochs)


@router.post("/rl/decide")
def rl_decide(body: RLDecideBody):
    """产出一次 RL 影子决策（绝不执行下单）。"""
    from backend.services.learning_core.rl_core.shadow import shadow_service
    return shadow_service.decide(body.symbol, body.timeframe, position=body.position)


# ══════════════════════════════════════════════════════════════
#  opencode 治理 codegen（受控管道）
# ══════════════════════════════════════════════════════════════

class CodegenAssistBody(BaseModel):
    model_config = {"protected_namespaces": ()}
    prompt: str
    model_slug: str = "deepseek/deepseek-chat"


class CodegenProposeBody(BaseModel):
    model_config = {"protected_namespaces": ()}
    name: str
    spec: str
    kind: str = "factor"
    model_slug: str = "deepseek/deepseek-chat"


@router.post("/codegen/assist")
def codegen_assist(body: CodegenAssistBody):
    """开发期 LLM 助手（直接返回代码文本，不落盘）。"""
    from backend.services.learning_core.codegen import governed_codegen
    return governed_codegen.assist(body.prompt, model_slug=body.model_slug)


@router.post("/codegen/propose")
def codegen_propose(body: CodegenProposeBody):
    """生成因子/策略 .py 提案（写入隔离沙箱，等待审批）。"""
    from backend.services.learning_core.codegen import governed_codegen
    return governed_codegen.propose(name=body.name, spec=body.spec, kind=body.kind, model_slug=body.model_slug)


@router.get("/codegen/proposals")
def codegen_list():
    from backend.services.learning_core.codegen import governed_codegen
    return {"proposals": governed_codegen.list_proposals()}


@router.get("/codegen/proposals/{proposal_id}")
def codegen_get(proposal_id: str):
    from backend.services.learning_core.codegen import governed_codegen
    p = governed_codegen.get_proposal(proposal_id)
    return p or {"error": "not found"}


@router.post("/codegen/proposals/{proposal_id}/approve")
def codegen_approve(proposal_id: str):
    """Governor 审批通过（仅标记可合入，不自动合并到主干）。"""
    from backend.services.learning_core.codegen import governed_codegen
    return governed_codegen.approve(proposal_id)


@router.post("/codegen/proposals/{proposal_id}/reject")
def codegen_reject(proposal_id: str):
    from backend.services.learning_core.codegen import governed_codegen
    return governed_codegen.reject(proposal_id)


@router.get("/scheduler")
def get_scheduler():
    """统一调度器状态（进化 + Hermes/OpenCode 时间轴聚合）。"""
    from backend.services.learning_core.scheduler_facade import unified_scheduler
    return unified_scheduler.status()


@router.post("/scheduler/trigger/{task}")
def trigger_scheduler(task: str):
    """统一调度器触发（evolution.weekly / evolution.hypothesis_scan / hermes.<task>）。"""
    from backend.services.learning_core import orchestrator
    return orchestrator.trigger_scheduled(task)


@router.get("/status")
def get_status():
    """内核 + 账本健康状态。"""
    from backend.services.learning_core import flags
    from backend.services.learning_core.ledger import ledger
    return {
        "core_enabled": flags.get_flag("LEARNING_CORE_ENABLED"),
        "ledger": ledger.stats(),
        "flags": flags.all_flags(),
    }


# ── [2026-10-04 工作流①②③] 证据报表接口（全部只读；样本不足时诚实输出，不产比率）──

@router.get("/shadow/report")
def shadow_report(limit: int = 500):
    """②③ 影子 vs 真实管线对比报表。

    数据源：账本 `evolution_lineage` 的 `rl_shadow` / `vol_target_shadow`（开仓时写入）。
    样本 < `SHADOW_*_MIN_SAMPLES`（默认 30）时返回 `insufficient_samples` + 门槛 + 提示，
    **不输出任何比率**（防"看着有报表、其实没数据"）。
    """
    from backend.services.learning_core.shadow_compare import combined
    return combined(limit=limit)


@router.get("/calibration/forward")
def calibration_forward(days: int = 12, horizon_days: int = 7, include_hold: bool = False):
    """① 前向收益标注 + 置信分桶校准表（只读）。

    实测（2026-10-04，n=335，7 天前向）：40-50 桶胜率 77.5%（收缩 64.3%，期望 +0.0030）
    vs 50-60 桶 23.9%（期望 -0.0387）⇒ **置信度与胜率在同车道内反相关**，
    与 `ai_decision_calibrator` 的 `cold_linear` 单调兜底方向相反。
    `include_hold=True` 会把 hold 反事实当看多（**不推荐**：master 车道 9,555 条 hold 的
    direction 全是 'hold'，无方向观点，这样做会污染结论）。
    """
    from backend.services.learning_core.forward_label import label_decisions
    res = label_decisions(days=days, horizon_days=horizon_days,
                          include_hold=include_hold, limit=3000)
    return {"labeled": res.get("labeled"), "skipped": res.get("skipped"),
            "horizon_days": horizon_days, "buckets": res.get("buckets"),
            "by_lane": res.get("by_lane")}
