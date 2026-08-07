"""
Paper 快速试单 + 学习激活 — 运行时控制中枢

支持前端仪表盘开关与参数热改；持久化到 data/paper_fast_trial.json。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from copy import deepcopy
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join("data", "paper_fast_trial.json")

# 参数定义：group 用于前端分组展示
PARAM_DEFS: List[Dict[str, Any]] = [
    # ── 总开关 ──
    {
        "key": "PAPER_FAST_TRIAL",
        "type": "bool",
        "group": "master",
        "label": "快速试单模式",
        "desc": "开启后加速 tick、放宽开单门控、激活学习闭环",
        "default": True,
    },
    {
        "key": "LEARNING_LOOP_ENABLED",
        "type": "bool",
        "group": "master",
        "label": "学习闭环总开关",
        "desc": "关闭后 LearningLoop 定时任务全部跳过",
        "default": True,
    },
    {
        "key": "MIDLONG_MLTO_CONTROLS_EXEC",
        "type": "bool",
        "group": "master",
        "label": "MLTO 控开单",
        "desc": "true=MLTO Hub 控开单；false（默认）=SwingAgent/TrendAgent 独立直控",
        "default": False,
    },
    # ── 三周期 Tick（协调器 vs AI 分析分离）──
    {
        "key": "TIER_TICK_SCHEDULER_ENABLED",
        "type": "bool",
        "group": "tier_tick",
        "label": "分层 Tick 调度",
        "desc": "开启后：协调器心跳快、中线/长线 AI 各自独立间隔；上一轮 LLM 未结束则整 tick 让位",
        "default": True,
    },
    {
        "key": "TIER_COORDINATOR_TICK_SEC",
        "type": "int",
        "group": "tier_tick",
        "label": "协调器心跳(秒)",
        "desc": "轻量 tick：学习集成、持仓巡检、解锁 —— 不含 LLM",
        "min": 15,
        "max": 120,
        "default": 45,
    },
    {
        "key": "TIER_MID_AI_TICK_SEC",
        "type": "int",
        "group": "tier_tick",
        "label": "中线 AI 间隔(秒)",
        "desc": "SwingAgent / MLTO 中线研判；paper 均衡默认 45s",
        "min": 45,
        "max": 600,
        "default": 45,
    },
    {
        "key": "TIER_LONG_AI_TICK_SEC",
        "type": "int",
        "group": "tier_tick",
        "label": "长线 AI 间隔(秒)",
        "desc": "TrendAgent / MLTO 长线研判；paper 均衡默认 90s",
        "min": 90,
        "max": 900,
        "default": 90,
    },
    # ── 节奏 ──
    {
        "key": "PAPER_PACE_GEAR",
        "type": "gear",
        "group": "pace",
        "label": "Paper 节奏档",
        "desc": "截流旋钮（每 tick 最多跑几个策略/币种）；分层 Tick 开启时不再等于 AI 分析频率",
        "default": "blitz",
        "options": ["blitz", "turbo", "warm", "balanced", "conservative"],
    },
    # ── 开单门控 ──
    {
        "key": "MIDLONG_OPEN_READINESS_MIN_MID",
        "type": "int",
        "group": "open_gate",
        "label": "中线就绪度门槛",
        "min": 20,
        "max": 80,
        "default": 35,
    },
    {
        "key": "MIDLONG_OPEN_READINESS_MIN_LONG",
        "type": "int",
        "group": "open_gate",
        "label": "长线就绪度门槛",
        "min": 20,
        "max": 85,
        "default": 40,
    },
    {
        "key": "MIDLONG_THESIS_MIN_REVIEWS",
        "type": "int",
        "group": "open_gate",
        "label": "最少复核次数",
        "min": 0,
        "max": 5,
        "default": 1,
    },
    {
        "key": "MIDLONG_PERSISTENCE_TICKS",
        "type": "int",
        "group": "open_gate",
        "label": "方向持久 tick",
        "desc": "0=跳过持久检查（立即试单）",
        "min": 0,
        "max": 5,
        "default": 1,
    },
    {
        "key": "MIDLONG_THESIS_STABLE_MIN_SEC_MID",
        "type": "int",
        "group": "open_gate",
        "label": "中线方向稳定秒数",
        "desc": "0=跳过（快速试单推荐）；1800=30分钟",
        "min": 0,
        "max": 7200,
        "default": 0,
    },
    {
        "key": "MIDLONG_THESIS_STABLE_MIN_SEC_LONG",
        "type": "int",
        "group": "open_gate",
        "label": "长线方向稳定秒数",
        "desc": "0=跳过稳定时间检查",
        "min": 0,
        "max": 7200,
        "default": 0,
    },
    {
        "key": "MIDLONG_THESIS_STALE_MAX_SEC",
        "type": "int",
        "group": "open_gate",
        "label": "thesis 新鲜度上限(秒)",
        "min": 60,
        "max": 3600,
        "default": 900,
    },
    {
        "key": "HUB_WAIT_MIN_ADJUSTED",
        "type": "float",
        "group": "open_gate",
        "label": "Hub WAIT 放宽阈值",
        "desc": "AI 模式下 Hub=WAIT 仍允许试探的最低 adjusted 分",
        "min": 0.2,
        "max": 0.6,
        "default": 0.35,
    },
    # ── 学习激活 ──
    {
        "key": "FULLAUTO_LEARNING_INTEGRATION_EVERY_N",
        "type": "int",
        "group": "learning",
        "label": "主循环学习集成间隔(tick)",
        "min": 1,
        "max": 20,
        "default": 1,
    },
    {
        "key": "LEARNING_LOOP_OUTCOME_INTERVAL_S",
        "type": "int",
        "group": "learning",
        "label": "平仓结果扫描间隔(秒)",
        "min": 30,
        "max": 3600,
        "default": 90,
    },
    {
        "key": "LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S",
        "type": "int",
        "group": "learning",
        "label": "Paper 回填间隔(秒)",
        "min": 30,
        "max": 3600,
        "default": 120,
    },
    {
        "key": "THESIS_POSTMORTEM_COOLDOWN_SEC",
        "type": "int",
        "group": "learning",
        "label": "MLTO 复盘节流(秒)",
        "min": 60,
        "max": 7200,
        "default": 300,
    },
    {
        "key": "MLTO_LEARNING_TICK_ENABLED",
        "type": "bool",
        "group": "learning",
        "label": "每 tick 异步学习兜底",
        "desc": "主循环内触发 paper_outcome_backfill + outcome_batch",
        "default": True,
    },
    # ── 短线 ScalpRouter ──
    {
        "key": "SCALP_FACTOR_INDEPENDENT_SCHEDULER",
        "type": "bool",
        "group": "scalp",
        "label": "短线独立调度",
        "desc": "ScalpRouter 与 AI 主循环解耦，每 45s 扫描因子",
        "default": True,
    },
    {
        "key": "SCALP_FACTOR_SCAN_INTERVAL_SEC",
        "type": "int",
        "group": "scalp",
        "label": "因子扫描间隔(秒)",
        "min": 20,
        "max": 300,
        "default": 45,
    },
    {
        "key": "SCALP_FACTOR_CONFIRM_THRESHOLD",
        "type": "int",
        "group": "scalp",
        "label": "因子探索门槛",
        "desc": "分数≥此值才考虑开单",
        "min": 15,
        "max": 70,
        "default": 35,
    },
    {
        "key": "SCALP_FACTOR_EXECUTE_THRESHOLD",
        "type": "int",
        "group": "scalp",
        "label": "因子直通门槛",
        "desc": "分数≥此值较高置信直通",
        "min": 20,
        "max": 80,
        "default": 45,
    },
    {
        "key": "SCALP_DIRECT_THRESHOLD",
        "type": "int",
        "group": "scalp",
        "label": "ExecutionGate 直通分",
        "min": 20,
        "max": 80,
        "default": 45,
    },
    {
        "key": "SCALP_VETO_BAND_LOW",
        "type": "int",
        "group": "scalp",
        "label": "ExecutionGate 最低分",
        "desc": "低于此分直接 hold",
        "min": 15,
        "max": 60,
        "default": 35,
    },
    {
        "key": "SCALP_OPEN_COOLDOWN_SEC",
        "type": "int",
        "group": "scalp",
        "label": "同币开仓冷却(秒)",
        "desc": "同一 symbol 任意方向两次开仓最小间隔",
        "min": 0,
        "max": 3600,
        "default": 300,
    },
    {
        "key": "SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC",
        "type": "int",
        "group": "scalp",
        "label": "同币同向冷却(秒)",
        "min": 0,
        "max": 14400,
        "default": 600,
    },
    {
        "key": "SCALP_MAX_OPENS_PER_TICK",
        "type": "int",
        "group": "scalp",
        "label": "每轮最大开仓数",
        "min": 1,
        "max": 10,
        "default": 1,
    },
    {
        "key": "TIER_SHORT_COOLDOWN_SEC",
        "type": "int",
        "group": "scalp",
        "label": "平仓后再开冷却(秒)",
        "desc": "短线 tier 平仓后同 symbol 再开仓等待时间（默认 4h）",
        "min": 0,
        "max": 86400,
        "default": 14400,
    },
    {
        "key": "SHORT_TIER_CONFIDENCE_EXTRA",
        "type": "int",
        "group": "scalp",
        "label": "短线置信度加成门槛",
        "desc": "硬门槛=基础50%+此值；0=不额外加",
        "min": 0,
        "max": 25,
        "default": 8,
    },
    {
        "key": "SHORT_TIER_SAME_DIR_COOLDOWN_S",
        "type": "int",
        "group": "scalp",
        "label": "连续同向短线冷却(秒)",
        "min": 0,
        "max": 86400,
        "default": 14400,
    },
    {
        "key": "SCALP_EXECUTION_LANE_ENABLED",
        "type": "bool",
        "group": "scalp",
        "label": "ScalpExecutionLane",
        "desc": "关闭则 ScalpRouter 规则门控全部跳过",
        "default": True,
    },
    {
        "key": "SCALP_VETO_FAIL_OPEN",
        "type": "bool",
        "group": "scalp",
        "label": "FlashVeto 超时放行",
        "desc": "LLM 否决超时时是否 fail-open 允许开单",
        "default": True,
    },
]

_GROUP_LABELS = {
    "master": "总开关",
    "tier_tick": "三周期 Tick",
    "pace": "交易节奏",
    "open_gate": "中线/长线门控",
    "scalp": "短线 ScalpRouter",
    "learning": "学习激活",
}

_GEAR_LABELS = {
    "blitz": "闪电(30s)",
    "turbo": "极速(45s)",
    "warm": "偏快(60s)",
    "balanced": "均衡(90s)",
    "conservative": "保守(120s)",
}

# 预设：patches 会覆盖 overrides；highlights 供前端卡片展示
PRESET_DEFS: List[Dict[str, Any]] = [
    {
        "id": "fast",
        "label": "快速试单",
        "desc": "全面加速：协调 30s + 学习每 tick + 短线降门槛，中线/长线 AI 保持 90/180s",
        "icon": "rocket",
        "accent": "violet",
        "highlights": ["协调 30s", "中线 AI 90s", "长线 AI 180s", "短线 30s", "门控放宽"],
        "patches": {
            "PAPER_FAST_TRIAL": True,
            "PAPER_PACE_GEAR": "blitz",
            "TIER_TICK_SCHEDULER_ENABLED": True,
            "TIER_COORDINATOR_TICK_SEC": 30,
            "TIER_MID_AI_TICK_SEC": 90,
            "TIER_LONG_AI_TICK_SEC": 180,
            "MIDLONG_OPEN_READINESS_MIN_MID": 35,
            "MIDLONG_OPEN_READINESS_MIN_LONG": 40,
            "MIDLONG_PERSISTENCE_TICKS": 0,
            "MIDLONG_THESIS_STABLE_MIN_SEC_MID": 0,
            "MIDLONG_THESIS_STABLE_MIN_SEC_LONG": 0,
            "MIDLONG_THESIS_MIN_REVIEWS": 1,
            "MIDLONG_THESIS_STALE_MAX_SEC": 900,
            "HUB_WAIT_MIN_ADJUSTED": 0.35,
            "FULLAUTO_LEARNING_INTEGRATION_EVERY_N": 1,
            "LEARNING_LOOP_OUTCOME_INTERVAL_S": 90,
            "LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S": 120,
            "THESIS_POSTMORTEM_COOLDOWN_SEC": 300,
            "MLTO_LEARNING_TICK_ENABLED": True,
            "LEARNING_LOOP_ENABLED": True,
            "SCALP_FACTOR_INDEPENDENT_SCHEDULER": True,
            "SCALP_FACTOR_SCAN_INTERVAL_SEC": 30,
            "SCALP_FACTOR_CONFIRM_THRESHOLD": 25,
            "SCALP_FACTOR_EXECUTE_THRESHOLD": 35,
            "SCALP_DIRECT_THRESHOLD": 35,
            "SCALP_VETO_BAND_LOW": 25,
            "SCALP_ORCH_CONFLICT_MIN_SCORE": 38,
            "SCALP_RANGE_MAX_LONG": 0.88,
            "SCALP_OPEN_COOLDOWN_SEC": 60,
            "SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC": 120,
            "SCALP_MAX_OPENS_PER_TICK": 3,
            "TIER_SHORT_COOLDOWN_SEC": 600,
            "SHORT_TIER_CONFIDENCE_EXTRA": 0,
            "SHORT_TIER_SAME_DIR_COOLDOWN_S": 600,
        },
    },
    {
        "id": "learning",
        "label": "学习优先",
        "desc": "堆样本喂学习环：协调器快、AI 不急，平仓扫描/回填最密",
        "icon": "brain",
        "accent": "emerald",
        "highlights": ["学习每 tick", "回填 90s", "中线 AI 120s", "门控适中"],
        "patches": {
            "PAPER_FAST_TRIAL": True,
            "PAPER_PACE_GEAR": "turbo",
            "TIER_TICK_SCHEDULER_ENABLED": True,
            "TIER_COORDINATOR_TICK_SEC": 30,
            "TIER_MID_AI_TICK_SEC": 120,
            "TIER_LONG_AI_TICK_SEC": 240,
            "MIDLONG_OPEN_READINESS_MIN_MID": 40,
            "MIDLONG_OPEN_READINESS_MIN_LONG": 45,
            "MIDLONG_PERSISTENCE_TICKS": 1,
            "MIDLONG_THESIS_STABLE_MIN_SEC_MID": 0,
            "MIDLONG_THESIS_STABLE_MIN_SEC_LONG": 0,
            "MIDLONG_THESIS_MIN_REVIEWS": 1,
            "MIDLONG_THESIS_STALE_MAX_SEC": 900,
            "HUB_WAIT_MIN_ADJUSTED": 0.38,
            "FULLAUTO_LEARNING_INTEGRATION_EVERY_N": 1,
            "LEARNING_LOOP_OUTCOME_INTERVAL_S": 60,
            "LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S": 90,
            "THESIS_POSTMORTEM_COOLDOWN_SEC": 180,
            "MLTO_LEARNING_TICK_ENABLED": True,
            "LEARNING_LOOP_ENABLED": True,
            "SCALP_FACTOR_INDEPENDENT_SCHEDULER": True,
            "SCALP_FACTOR_SCAN_INTERVAL_SEC": 45,
            "SCALP_FACTOR_CONFIRM_THRESHOLD": 30,
            "SCALP_FACTOR_EXECUTE_THRESHOLD": 40,
            "SCALP_DIRECT_THRESHOLD": 40,
            "SCALP_VETO_BAND_LOW": 30,
            "SCALP_ORCH_CONFLICT_MIN_SCORE": 38,
            "SCALP_RANGE_MAX_LONG": 0.88,
            "SCALP_OPEN_COOLDOWN_SEC": 120,
            "SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC": 240,
            "SCALP_MAX_OPENS_PER_TICK": 2,
            "TIER_SHORT_COOLDOWN_SEC": 1800,
            "SHORT_TIER_CONFIDENCE_EXTRA": 0,
            "SHORT_TIER_SAME_DIR_COOLDOWN_S": 3600,
        },
    },
    {
        "id": "scalp",
        "label": "短线刷量",
        "desc": "ScalpRouter 为主：因子扫描 25s、低门槛多开；中线/长线 AI 放慢",
        "icon": "zap",
        "accent": "amber",
        "highlights": ["短线 25s", "每轮最多 4 开", "中线 AI 180s", "长线 AI 300s"],
        "patches": {
            "PAPER_FAST_TRIAL": True,
            "PAPER_PACE_GEAR": "blitz",
            "TIER_TICK_SCHEDULER_ENABLED": True,
            "TIER_COORDINATOR_TICK_SEC": 30,
            "TIER_MID_AI_TICK_SEC": 180,
            "TIER_LONG_AI_TICK_SEC": 300,
            "MIDLONG_OPEN_READINESS_MIN_MID": 50,
            "MIDLONG_OPEN_READINESS_MIN_LONG": 55,
            "MIDLONG_PERSISTENCE_TICKS": 2,
            "MIDLONG_THESIS_STABLE_MIN_SEC_MID": 600,
            "MIDLONG_THESIS_STABLE_MIN_SEC_LONG": 900,
            "MIDLONG_THESIS_MIN_REVIEWS": 2,
            "MIDLONG_THESIS_STALE_MAX_SEC": 600,
            "HUB_WAIT_MIN_ADJUSTED": 0.42,
            "FULLAUTO_LEARNING_INTEGRATION_EVERY_N": 2,
            "LEARNING_LOOP_OUTCOME_INTERVAL_S": 120,
            "LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S": 180,
            "THESIS_POSTMORTEM_COOLDOWN_SEC": 600,
            "MLTO_LEARNING_TICK_ENABLED": True,
            "LEARNING_LOOP_ENABLED": True,
            "SCALP_FACTOR_INDEPENDENT_SCHEDULER": True,
            "SCALP_FACTOR_SCAN_INTERVAL_SEC": 25,
            "SCALP_FACTOR_CONFIRM_THRESHOLD": 20,
            "SCALP_FACTOR_EXECUTE_THRESHOLD": 30,
            "SCALP_DIRECT_THRESHOLD": 30,
            "SCALP_VETO_BAND_LOW": 20,
            "SCALP_ORCH_CONFLICT_MIN_SCORE": 35,
            "SCALP_RANGE_MAX_LONG": 0.92,
            "SCALP_OPEN_COOLDOWN_SEC": 45,
            "SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC": 90,
            "SCALP_MAX_OPENS_PER_TICK": 4,
            "TIER_SHORT_COOLDOWN_SEC": 300,
            "SHORT_TIER_CONFIDENCE_EXTRA": 0,
            "SHORT_TIER_SAME_DIR_COOLDOWN_S": 300,
        },
    },
    {
        "id": "midlong",
        "label": "中长线验证",
        "desc": "Agent 直控验证：Swing/Trend 独立 + 组合门控，MLTO 仅面板",
        "icon": "target",
        "accent": "sky",
        "highlights": ["中线 AI 45s", "长线 AI 90s", "Agent Fast Lane", "MLTO 不控单"],
        "patches": {
            "PAPER_FAST_TRIAL": True,
            "PAPER_PACE_GEAR": "warm",
            "TIER_TICK_SCHEDULER_ENABLED": True,
            "TIER_COORDINATOR_TICK_SEC": 45,
            "TIER_MID_AI_TICK_SEC": 45,
            "TIER_LONG_AI_TICK_SEC": 90,
            "MIDLONG_MLTO_CONTROLS_EXEC": False,
            "MIDLONG_THESIS_OPEN_GATE": False,
            "MIDLONG_AI_MANDATORY": True,
            "MIDLONG_AGENT_INDEPENDENT_SCHEDULER": True,
            "MIDLONG_QUANT_BRIEF_HARD_GATE": False,
            "AGENT_FACT_GUARD_PAPER_ENFORCE": False,
            "ORCHESTRATOR_HARD_GATE": False,
            "MIDLONG_OPEN_READINESS_MIN_MID": 25,
            "MIDLONG_OPEN_READINESS_MIN_LONG": 28,
            "MIDLONG_PERSISTENCE_TICKS": 1,
            "V5_TREND_FOLLOW_MIN_CONFIDENCE": 50,
            "LAYER_BUDGET_SCALP": 0.40,
            # 中长线合并：swing 层并入 trend（0.45 + 0.15 → trend 0.60）
            "LAYER_BUDGET_TREND": 0.60,
            "TREND_MAX_OPENS_PER_WEEK": 2,
            "MIDLONG_MONTE_CARLO_ENABLED": True,
            "MIDLONG_THESIS_STABLE_MIN_SEC_MID": 0,
            "MIDLONG_THESIS_STABLE_MIN_SEC_LONG": 0,
            "MIDLONG_THESIS_MIN_REVIEWS": 1,
            "MIDLONG_THESIS_STALE_MAX_SEC": 900,
            "FULLAUTO_LEARNING_INTEGRATION_EVERY_N": 2,
            "LEARNING_LOOP_OUTCOME_INTERVAL_S": 180,
            "LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S": 300,
            "THESIS_POSTMORTEM_COOLDOWN_SEC": 300,
            "MLTO_LEARNING_TICK_ENABLED": True,
            "LEARNING_LOOP_ENABLED": True,
            "SCALP_FACTOR_INDEPENDENT_SCHEDULER": True,
            "SCALP_FACTOR_SCAN_INTERVAL_SEC": 60,
            "SCALP_OPEN_COOLDOWN_SEC": 120,
            "PAPER_ONE_WAY_REVERSE_NETTING": False,
        },
    },
    {
        "id": "balanced",
        "label": "均衡模式",
        "desc": "日常模拟：标准三周期节奏 + 正常门控，适合长时间挂着观察",
        "icon": "scale",
        "accent": "slate",
        "highlights": ["协调 45s", "中线 AI 120s", "长线 AI 240s", "标准冷却"],
        "patches": {
            "PAPER_FAST_TRIAL": False,
            "PAPER_PACE_GEAR": "balanced",
            "TIER_TICK_SCHEDULER_ENABLED": True,
            "TIER_COORDINATOR_TICK_SEC": 45,
            "TIER_MID_AI_TICK_SEC": 120,
            "TIER_LONG_AI_TICK_SEC": 240,
            "MIDLONG_OPEN_READINESS_MIN_MID": 45,
            "MIDLONG_OPEN_READINESS_MIN_LONG": 50,
            "MIDLONG_PERSISTENCE_TICKS": 1,
            "MIDLONG_THESIS_STABLE_MIN_SEC_MID": 0,
            "MIDLONG_THESIS_STABLE_MIN_SEC_LONG": 300,
            "MIDLONG_THESIS_MIN_REVIEWS": 1,
            "MIDLONG_THESIS_STALE_MAX_SEC": 900,
            "HUB_WAIT_MIN_ADJUSTED": 0.42,
            "FULLAUTO_LEARNING_INTEGRATION_EVERY_N": 5,
            "LEARNING_LOOP_OUTCOME_INTERVAL_S": 300,
            "LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S": 600,
            "THESIS_POSTMORTEM_COOLDOWN_SEC": 3600,
            "MLTO_LEARNING_TICK_ENABLED": False,
            "LEARNING_LOOP_ENABLED": True,
            "SCALP_FACTOR_INDEPENDENT_SCHEDULER": True,
            "SCALP_FACTOR_SCAN_INTERVAL_SEC": 45,
            "SCALP_FACTOR_CONFIRM_THRESHOLD": 35,
            "SCALP_FACTOR_EXECUTE_THRESHOLD": 45,
            "SCALP_DIRECT_THRESHOLD": 45,
            "SCALP_VETO_BAND_LOW": 35,
            "SCALP_OPEN_COOLDOWN_SEC": 300,
            "SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC": 600,
            "SCALP_MAX_OPENS_PER_TICK": 1,
            "TIER_SHORT_COOLDOWN_SEC": 14400,
            "SHORT_TIER_CONFIDENCE_EXTRA": 8,
            "SHORT_TIER_SAME_DIR_COOLDOWN_S": 14400,
        },
    },
    {
        "id": "conservative",
        "label": "保守模拟",
        "desc": "接近实盘：慢 tick、严门控、少开单，用于验证策略稳定性",
        "icon": "shield",
        "accent": "rose",
        "highlights": ["协调 60s", "中线 AI 150s", "长线 AI 300s", "严门控"],
        "patches": {
            "PAPER_FAST_TRIAL": False,
            "PAPER_PACE_GEAR": "conservative",
            "TIER_TICK_SCHEDULER_ENABLED": True,
            "TIER_COORDINATOR_TICK_SEC": 60,
            "TIER_MID_AI_TICK_SEC": 150,
            "TIER_LONG_AI_TICK_SEC": 300,
            "MIDLONG_OPEN_READINESS_MIN_MID": 50,
            "MIDLONG_OPEN_READINESS_MIN_LONG": 55,
            "MIDLONG_PERSISTENCE_TICKS": 2,
            "MIDLONG_THESIS_STABLE_MIN_SEC_MID": 900,
            "MIDLONG_THESIS_STABLE_MIN_SEC_LONG": 1800,
            "MIDLONG_THESIS_MIN_REVIEWS": 2,
            "MIDLONG_THESIS_STALE_MAX_SEC": 600,
            "HUB_WAIT_MIN_ADJUSTED": 0.45,
            "FULLAUTO_LEARNING_INTEGRATION_EVERY_N": 10,
            "LEARNING_LOOP_OUTCOME_INTERVAL_S": 600,
            "LEARNING_LOOP_PAPER_BACKFILL_INTERVAL_S": 1200,
            "THESIS_POSTMORTEM_COOLDOWN_SEC": 7200,
            "MLTO_LEARNING_TICK_ENABLED": False,
            "LEARNING_LOOP_ENABLED": True,
            "SCALP_FACTOR_INDEPENDENT_SCHEDULER": True,
            "SCALP_FACTOR_SCAN_INTERVAL_SEC": 60,
            "SCALP_FACTOR_CONFIRM_THRESHOLD": 40,
            "SCALP_FACTOR_EXECUTE_THRESHOLD": 50,
            "SCALP_DIRECT_THRESHOLD": 50,
            "SCALP_VETO_BAND_LOW": 40,
            "SCALP_OPEN_COOLDOWN_SEC": 600,
            "SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC": 1200,
            "SCALP_MAX_OPENS_PER_TICK": 1,
            "TIER_SHORT_COOLDOWN_SEC": 28800,
            "SHORT_TIER_CONFIDENCE_EXTRA": 10,
            "SHORT_TIER_SAME_DIR_COOLDOWN_S": 28800,
        },
    },
]

_PRESET_BY_ID: Dict[str, Dict[str, Any]] = {p["id"]: p for p in PRESET_DEFS}


def _env_defaults() -> Dict[str, Any]:
    """从 settings 模块读取当前有效默认值。"""
    try:
        from backend.config import settings
        out: Dict[str, Any] = {}
        for p in PARAM_DEFS:
            k = p["key"]
            if k == "PAPER_PACE_GEAR":
                try:
                    from backend.services.paper_pace_controller import paper_pace_controller
                    out[k] = paper_pace_controller.gear
                except Exception:
                    out[k] = p["default"]
            elif k == "HUB_WAIT_MIN_ADJUSTED":
                out[k] = 0.35 if getattr(settings, "PAPER_FAST_TRIAL", False) else 0.42
            elif k == "MLTO_LEARNING_TICK_ENABLED":
                out[k] = bool(getattr(settings, "PAPER_FAST_TRIAL", False))
            elif k == "TIER_SHORT_COOLDOWN_SEC":
                try:
                    out[k] = int(settings.TIER_PROTECTION_PARAMS["short"]["cooldown_sec"])
                except Exception:
                    out[k] = 14400
            else:
                out[k] = getattr(settings, k, p["default"])
        return out
    except Exception as exc:
        logger.debug("[PaperFastTrial] env defaults: %s", exc)
        return {p["key"]: p["default"] for p in PARAM_DEFS}


class PaperFastTrialController:
    _instance: Optional["PaperFastTrialController"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._overrides: Dict[str, Any] = {}
        self._active_preset: Optional[str] = None
        self._load_file()
        self.apply_all()

    def _load_file(self) -> None:
        if not os.path.isfile(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data.get("overrides"), dict):
                self._overrides = data["overrides"]
            ap = data.get("active_preset")
            if isinstance(ap, str) and ap in _PRESET_BY_ID:
                self._active_preset = ap
        except Exception as exc:
            logger.warning("[PaperFastTrial] 加载配置失败: %s", exc)

    def _save_file(self) -> None:
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE) or "data", exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"overrides": self._overrides, "active_preset": self._active_preset},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("[PaperFastTrial] 保存配置失败: %s", exc)

    def get_effective(self, key: str) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        defaults = _env_defaults()
        return defaults.get(key, next((p["default"] for p in PARAM_DEFS if p["key"] == key), None))

    def get_all_effective(self) -> Dict[str, Any]:
        return {p["key"]: self.get_effective(p["key"]) for p in PARAM_DEFS}

    def apply_all(self) -> None:
        """把有效值写入 settings + pace gear。"""
        try:
            import backend.config.settings as settings
            eff = self.get_all_effective()
            for k, v in eff.items():
                if k == "PAPER_PACE_GEAR":
                    continue
                if k == "HUB_WAIT_MIN_ADJUSTED":
                    setattr(settings, "_HUB_WAIT_MIN_ADJUSTED", float(v))
                    continue
                if k == "MLTO_LEARNING_TICK_ENABLED":
                    setattr(settings, "_MLTO_LEARNING_TICK_ENABLED", bool(v))
                    continue
                if hasattr(settings, k) or k in {p["key"] for p in PARAM_DEFS}:
                    setattr(settings, k, v)
            # 同步嵌套 tier 冷却（reentry_cooldown 读 TIER_PROTECTION_PARAMS）
            if "TIER_SHORT_COOLDOWN_SEC" in eff:
                try:
                    cd = int(eff["TIER_SHORT_COOLDOWN_SEC"])
                    settings.TIER_PROTECTION_PARAMS["short"]["cooldown_sec"] = cd
                except Exception:
                    pass
            gear = str(eff.get("PAPER_PACE_GEAR") or "blitz")
            from backend.services.paper_pace_controller import paper_pace_controller
            if gear in ("blitz", "turbo", "warm", "balanced", "conservative"):
                if "PAPER_PACE_GEAR" in self._overrides or paper_pace_controller.gear != gear:
                    paper_pace_controller.set_gear(
                        gear,
                        manual=True,
                        reason="fast_trial_apply",
                    )
            logger.info("[PaperFastTrial] 已应用配置 fast_trial=%s gear=%s", eff.get("PAPER_FAST_TRIAL"), gear)
            try:
                from backend.services.full_auto_trading_service import full_auto_trading_service
                full_auto_trading_service._on_pace_gear_change("tier", "tick_update")
            except Exception:
                pass
        except Exception as exc:
            logger.warning("[PaperFastTrial] apply_all 失败: %s", exc)

    def update(self, patches: Dict[str, Any]) -> Dict[str, Any]:
        """批量更新参数并应用。"""
        schema_keys = {p["key"] for p in PARAM_DEFS}
        for k, v in patches.items():
            if k not in schema_keys:
                continue
            spec = next(p for p in PARAM_DEFS if p["key"] == k)
            self._overrides[k] = self._coerce_value(spec, v)
        self._active_preset = None
        self._save_file()
        self.apply_all()
        return self.to_dict()

    def apply_preset(self, preset: str) -> Dict[str, Any]:
        spec = _PRESET_BY_ID.get(preset)
        if not spec:
            raise ValueError(f"unknown preset: {preset}")
        patches = dict(spec.get("patches") or {})
        schema_keys = {p["key"] for p in PARAM_DEFS}
        self._overrides = {
            k: self._coerce_value(next(p for p in PARAM_DEFS if p["key"] == k), v)
            for k, v in patches.items()
            if k in schema_keys
        }
        self._active_preset = preset
        self._save_file()
        self.apply_all()
        logger.info("[PaperFastTrial] 已应用预设 %s", preset)
        return self.to_dict()

    @staticmethod
    def _coerce_value(spec: Dict[str, Any], v: Any) -> Any:
        t = spec.get("type")
        if t == "bool":
            return bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes", "on")
        if t == "int":
            iv = int(v)
            return max(spec.get("min", iv), min(spec.get("max", iv), iv))
        if t == "float":
            fv = float(v)
            return max(spec.get("min", fv), min(spec.get("max", fv), fv))
        if t == "gear":
            g = str(v).lower()
            opts = spec.get("options") or []
            return g if g in opts else spec.get("default", "balanced")
        return v

    def _dashboard_metrics(self) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {}
        try:
            from backend.services.paper_pace_controller import paper_pace_controller
            metrics["pace"] = paper_pace_controller.to_dict()
        except Exception:
            metrics["pace"] = {}
        try:
            from backend.services.learning_bus import get_learning_bus
            metrics["learning_bus"] = get_learning_bus().get_status()
        except Exception:
            metrics["learning_bus"] = {}
        try:
            from backend.services.learning_loop_service import learning_loop
            metrics["learning_loop"] = {
                "paused": learning_loop.is_paused,
                "registered": getattr(learning_loop, "_registered", False),
                "last_tick_at": {
                    k: (v.isoformat() if v else None)
                    for k, v in (getattr(learning_loop, "_last_tick_at", {}) or {}).items()
                },
            }
        except Exception:
            metrics["learning_loop"] = {}
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.services.mlto.db_models import MltoThesis
            adb = AnalyticsSessionLocal()
            try:
                rows = adb.query(MltoThesis).limit(200).all()
                total = len(rows)
                with_llm = sum(
                    1 for r in rows
                    if (getattr(r, "thesis_summary", None) or "").strip()
                )
                min_ready = 35
                try:
                    from backend.config import settings
                    min_ready = int(getattr(settings, "MIDLONG_OPEN_READINESS_MIN_MID", 35) or 35)
                except Exception:
                    pass
                can_open = sum(
                    1 for r in rows
                    if (getattr(r, "thesis_summary", None) or "").strip()
                    and str(getattr(r, "direction", "") or "").lower() not in ("", "neutral")
                    and int(getattr(r, "open_readiness", 0) or 0) >= min_ready
                    and int(getattr(r, "review_count", 0) or 0) >= 1
                )
                metrics["mlto"] = {
                    "thesis_total": total,
                    "can_open": can_open,
                    "with_llm_summary": with_llm,
                }
            finally:
                adb.close()
        except Exception:
            metrics["mlto"] = {"thesis_total": 0, "can_open": 0, "with_llm_summary": 0}
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import FullAutoSession
            db = SessionLocal()
            try:
                running = db.query(FullAutoSession).filter(
                    FullAutoSession.status.in_(("running", "defensive"))
                ).count()
                metrics["sessions_running"] = running
            finally:
                db.close()
        except Exception:
            metrics["sessions_running"] = 0
        try:
            from backend.config import settings as _s
            metrics["scalp"] = {
                "confirm_threshold": int(getattr(_s, "SCALP_FACTOR_CONFIRM_THRESHOLD", 35)),
                "execute_threshold": int(getattr(_s, "SCALP_FACTOR_EXECUTE_THRESHOLD", 45)),
                "open_cooldown_sec": int(getattr(_s, "SCALP_OPEN_COOLDOWN_SEC", 300)),
                "reentry_cooldown_sec": int(
                    _s.TIER_PROTECTION_PARAMS.get("short", {}).get("cooldown_sec", 14400)
                ),
                "independent_scheduler": bool(getattr(_s, "SCALP_FACTOR_INDEPENDENT_SCHEDULER", True)),
            }
        except Exception:
            metrics["scalp"] = {}
        try:
            from backend.services.tier_tick_scheduler import get_intervals, status as tier_status
            from backend.config import settings as _ts
            metrics["tier_tick"] = {
                "scheduler_enabled": bool(getattr(_ts, "TIER_TICK_SCHEDULER_ENABLED", True)),
                "intervals_sec": get_intervals(),
                "note": "short=ScalpRouter独立; coordinator=轻量心跳不含LLM",
            }
            try:
                from backend.database.connection import SessionLocal
                from backend.database.models import FullAutoSession
                _tdb = SessionLocal()
                try:
                    _sid = (
                        _tdb.query(FullAutoSession.session_id)
                        .filter(FullAutoSession.status.in_(("running", "defensive")))
                        .order_by(FullAutoSession.updated_at.desc())
                        .limit(1)
                        .scalar()
                    )
                    if _sid:
                        metrics["tier_tick"]["live"] = tier_status(_sid)
                finally:
                    _tdb.close()
            except Exception:
                pass
        except Exception:
            metrics["tier_tick"] = {}
        return metrics

    def schema(self) -> Dict[str, Any]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for p in PARAM_DEFS:
            g = p["group"]
            entry = deepcopy(p)
            entry["effective"] = self.get_effective(p["key"])
            entry["overridden"] = p["key"] in self._overrides
            if p["type"] == "gear":
                entry["gear_labels"] = _GEAR_LABELS
            groups.setdefault(g, []).append(entry)
        return {
            "groups": [
                {"id": gid, "label": _GROUP_LABELS.get(gid, gid), "params": groups[gid]}
                for gid in ("master", "tier_tick", "pace", "open_gate", "scalp", "learning")
                if gid in groups
            ],
        }

    def to_dict(self) -> Dict[str, Any]:
        eff = self.get_all_effective()
        return {
            "enabled": bool(eff.get("PAPER_FAST_TRIAL")),
            "active_preset": self._active_preset,
            "effective": eff,
            "overrides": dict(self._overrides),
            "schema": self.schema(),
            "dashboard": self._dashboard_metrics(),
            "presets": [
                {
                    "id": p["id"],
                    "label": p["label"],
                    "desc": p["desc"],
                    "icon": p.get("icon", "rocket"),
                    "accent": p.get("accent", "slate"),
                    "highlights": list(p.get("highlights") or []),
                }
                for p in PRESET_DEFS
            ],
        }


paper_fast_trial_controller = PaperFastTrialController()


def get_hub_wait_min_adjusted() -> float:
    try:
        from backend.config import settings
        return float(getattr(settings, "_HUB_WAIT_MIN_ADJUSTED", None) or (
            0.35 if getattr(settings, "PAPER_FAST_TRIAL", False) else 0.42
        ))
    except Exception:
        return 0.35


def mlto_learning_tick_enabled() -> bool:
    try:
        from backend.config import settings
        if hasattr(settings, "_MLTO_LEARNING_TICK_ENABLED"):
            return bool(settings._MLTO_LEARNING_TICK_ENABLED)
        return bool(getattr(settings, "PAPER_FAST_TRIAL", False))
    except Exception:
        return False
