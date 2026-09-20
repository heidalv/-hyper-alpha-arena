# -*- coding: utf-8 -*-
"""Agent Wall —— 画布式多 Agent 分析墙的数据层（只读）。

设计文档：`docs/Agent画布模块设计_20260919.md`
排查依据：`docs/Agent运行排查_20260919.md`（所有节点/边/死链都以此为据，2026-09-19 实测）

## 这个模块解决什么
用户诉求原话：「没有前端滚屏展示他们的分析，这个是完全错误的设计……每个 agent 都有一个（滚屏），
按照他们的边界分组，在一页显示，有大有小……画布一样，可以缩放、拖拽排列，用线链接看到关联关系」。

因此本模块提供三件事（**全部只读**）：
1. `build_state()` —— 画布骨架：节点（分组/尺寸/期望频率/实测状态）+ 边（类型/健康四态）+ 分组摘要；
2. `tail()` —— **增量**取每个节点的分析流（游标式），多节点一次返回（禁止每节点一个请求）；
3. `audit()` —— 断链与逻辑错误清单（把"算好了没人看"的东西摊开）。

## 健康口径（**复用 `job_registry`，不另造真相**）
`backend/services/ops/job_registry.py` 已有 `expected_interval_sec` + `stale` 判定（>2× 间隔 ⇒ warn/critical；
从未运行 ⇒ never_ran）。本模块直接采用其结论，映射为画布状态：
    None→ok    warn→stale    critical→dead    never_ran→never    （另加 disabled / static）
节点额外的"事实"（产物 mtime、最近成功时间）用于显示与审计，**不覆盖**上面的判定。

## 硬约束（吸取本项目 GIL 教训）
- `tail()` 对日志文件**必须增量读**（维护 offset），单次每节点最多 `_MAX_BYTES_PER_NODE` 字节；
  `logs/brain_subprocess.log` 已有 94.6 万行/146MB，禁止每请求全量扫描。
- 单页轮询只允许两个请求：`/state`（2~3s）+ `/tail`（2s，含全部可见节点）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
DATA_AGENTS = ROOT / "backend" / "data" / "agents"

#: 单节点单次最多返回的行数 / 字节数（防一次拉爆 146MB 的 brain 日志）
MAX_LINES_PER_NODE = 200
_MAX_BYTES_PER_NODE = 64 * 1024
#: 首读反向扫描预算：从文件尾部往前最多读这么多字节省（brain 日志 146MB，禁止全扫）
_SCAN_BUDGET = int(os.getenv("AGENT_WALL_SCAN_BUDGET", str(4 * 1024 * 1024)))

#: 文件读取偏移缓存：{(path, node_id): (offset, inode)}
_OFFSETS: Dict[str, Tuple[int, int]] = {}

# ─────────────────────────── 节点注册表 ───────────────────────────
# size: XL / L / M / S（画布尺寸档）；status_hint 仅用于"结构性事实"（dead/disabled），
# 运行状态一律由 job_registry + 产物 mtime 现场判定。
NODES: List[Dict[str, Any]] = [
    # ── G0 数据底座 ──
    {"id": "data_center", "group": "G0", "size": "M", "label": "数据中心",
     "role": "DC_ONLY 唯一行情源（:9100）",
     "cadence_label": "常驻", "source": {"kind": "file", "path": "logs/data-center.log"},
     "deps": []},
    {"id": "kline_collector", "group": "G0", "size": "S", "label": "K线采集",
     "role": "实时采集 + 覆盖守卫",
     "cadence_label": "轮级", "source": {"kind": "file", "path": "logs/data-center.log", "filter": "collect|Freshness"},
     "deps": ["data_center"]},
    {"id": "factor_engine", "group": "G0", "size": "S", "label": "因子计算",
     "role": "因子引擎（被主脑/路线调用）",
     "cadence_label": "按需", "source": {"kind": "file", "path": "logs/backend.log", "filter": "factor_calculator"},
     "deps": ["kline_collector"]},
    {"id": "coin_select", "group": "G0", "size": "S", "label": "选币",
     "role": "AI 选币（喂主脑候选池）",
     "cadence_label": "常驻", "source": {"kind": "file", "path": "logs/backend.log", "filter": "AutoCoinSelector"},
     "deps": ["kline_collector"]},

    # ── G1 主脑（LLM 论题主脑）──
    {"id": "brain_mid", "group": "G1", "size": "XL", "label": "中线主脑",
     "role": "LLM 论题主脑（tier=mid，权威=llm_thesis）",
     "cadence_label": "实测 p50=74s（抢同一子进程池；180s 为死配置）", "expected_interval_s": 74,
     "source": {"kind": "file", "path": "logs/brain_subprocess.log", "filter": r"tier=mid"},
     "deps": ["factor_engine", "coin_select"]},
    {"id": "brain_long", "group": "G1", "size": "XL", "label": "长线主脑",
     "role": "LLM 论题主脑（tier=long）；**注意**：其 tick 上限 240s 是死配置（mark_tier_run 只标 short ⇒ mid/long 恒 due），"
             "实测与 mid 同频 ≈182s",
     "cadence_label": "实测 p50=71s（抢同一子进程池；240s 为死配置）", "expected_interval_s": 71,
     "source": {"kind": "file", "path": "logs/brain_subprocess.log", "filter": r"tier=long"},
     "deps": ["factor_engine", "coin_select"]},
    {"id": "coordinator", "group": "G1", "size": "L", "label": "协调器",
     "role": "统一 tick / 车道派发",
     "cadence_label": "30s", "expected_interval_s": 30,
     "source": {"kind": "file", "path": "logs/backend.log", "filter": "FullAutoOrchestrator|coordinator"},
     "deps": []},
    {"id": "thesis_store", "group": "G1", "size": "L", "label": "论题库（中线/长线共用）",
     "role": "mlto_thesis（30 条滚动）+ 事件流 mlto_thesis_events；mid/long 两条车道的决策来源",
     "cadence_label": "随主脑",
     "source": {"kind": "json_thesis", "session": os.getenv("AGENT_WALL_SESSION", "fa_7e12e7a1b6")},
     "deps": ["brain_mid", "brain_long"]},

    # ── G2 观察型 Agent 群 ──
    {"id": "anomaly", "group": "G2", "size": "M", "label": "异常检测",
     "role": "六通道 z/CUSUM → TradingState 建议",
     "cadence_label": "900s", "expected_interval_s": 900, "job": "agent_anomaly",
     "source": {"kind": "json_latest", "agent": "anomaly"}, "deps": ["kline_collector"]},
    {"id": "signal_review", "group": "G2", "size": "M", "label": "信号复盘",
     "role": "各信号源命中率/IC/半衰期",
     "cadence_label": "每日 BJ 08:00", "expected_interval_s": 86400, "job": "agent_signal_review",
     "source": {"kind": "json_latest", "agent": "signal_review"}, "deps": ["factor_engine"]},
    {"id": "event_impact", "group": "G2", "size": "M", "label": "事件冲击",
     "role": "事件显著性跟踪 + 可证伪预测",
     "cadence_label": "每日 BJ 06:30", "expected_interval_s": 86400, "job": "agent_event_impact",
     "source": {"kind": "json_latest", "agent": "event_impact"}, "deps": ["kline_collector"]},
    {"id": "execution_qa", "group": "G2", "size": "M", "label": "执行体检",
     "role": "滑点/拒单率/部分成交/实际费率",
     "cadence_label": "每日 BJ 07:15", "expected_interval_s": 86400, "job": "agent_execution_qa",
     "source": {"kind": "json_latest", "agent": "execution_qa"}, "deps": []},
    {"id": "timing", "group": "G2", "size": "S", "label": "择时（已停用）",
     "role": "regime 分类 + 三桶权重（E4 消费）",
     "cadence_label": "2026-09-05 起停用", "job": "agent_timing", "status_hint": "disabled",
     "source": {"kind": "json_latest", "agent": "timing"}, "deps": []},
    {"id": "param_search", "group": "G2", "size": "S", "label": "参数扫描（停用）",
     "role": "样本外切分 + 多重比较惩罚",
     "cadence_label": "AGENT_PARAM_SEARCH_ENABLED=false", "job": "agent_param_search",
     "status_hint": "disabled",
     "source": {"kind": "json_latest", "agent": "param_search"}, "deps": []},
    {"id": "experiment_advance", "group": "G2", "size": "S", "label": "实验卡推进",
     "role": "实验卡生命周期（到期判定）",
     "cadence_label": "3600s", "expected_interval_s": 3600, "job": "experiment_advance",
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\[experiments\]"},
     "deps": ["anomaly", "signal_review", "event_impact", "execution_qa"]},

    # ── G3 已移除：QAA v3 卡片编排层（9 张卡）于 2026-09-19 **正式退役**，
    #    故不在画布上列出（不画永远不运行的空节点）。决策与证据见 docs/ADR_QAA退役_20260919.md；
    #    前端以 retired_layers 字段说明"为何这里没有 QAA"，避免下次又有人问。

    # ── G4 车道与执行 ──
    # [2026-09-19 修正] 中/长线**不是两条独立车道**，而是同一个循环的两个 tier：
    # `fullauto_midlong_fa_7e12e7a1b6`（midlong_loop.py:79，实测 p50=157s/tick）驱动两个 tier 的子进程，
    # 子进程再驱动 trend_agent 的持仓复核。此前画成 coordinator→brain/trend 是错的（实测 0 穿透）。
    {"id": "midlong_loop", "group": "G4", "size": "L", "label": "中/长线主循环（midlong_loop）",
     "role": "**两条车道的共同驱动**：APScheduler job `fullauto_midlong_<sid>`；"
             "每 tick 派发 mid/long 主脑子进程 + 触发 trend_agent 持仓复核。"
             "TIER_MID/LONG_AI_TICK_SEC（180/240s）**都是死配置**（mark_tier_run 只标 short）",
     "cadence_label": "实测 p50=157s/tick（声明 180/240s 均不生效）", "expected_interval_s": 157,
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"MidLongAgent独立|mlto_cycle"},
     "deps": ["coordinator"]},
    {"id": "trend_e1_engine", "group": "G4", "size": "L", "label": "长线 E1 开仓（唯一长线 Writer）",
     "role": "**长线开仓唯一实现**：cron `v3_trend_e1_daily`（BJ 08:20）→ `paper_engine.place_order`，"
             "**绕过 Single Writer**；现存 4 笔 open 仓全为 `strategy_id=trend_e1:*`（ETH/SOL/LINK/BTC）",
     "cadence_label": "每日 BJ 08:20（cron）", "expected_interval_s": 86400,
     # [轮124 2026-09-19] 原过滤 `TrendE1|trend_e1` 取 `logs/backend.log`，实测命中
     # 124 行**全是本卡自己的轮询访问日志**（`uvicorn.access ... "/api/agent-wall/tail?
     # nodes=...,trend_e1_engine,..."`），真实 E1 业务行 0 条 ⇒ 卡片内容与长线无关。
     # 改为读 **E1 最近一次运行产物**（cron 每天 08:20 写一次）。
     "source": {"kind": "json_file", "path": "backend/data/trend_drift/e1_last_run.json"},
     "deps": ["thesis_store"]},
    # [轮127 2026-09-19 用户指令] 「中线因子路线 A/B（已停用）」节点**已删除**：
    #   它当时只是个"已停用"占位（source=kind:none、0 行），用户看过截图后要求去掉。
    #   因子路线本身仍只产证据、不开仓（.env MIDLONG_MID_FACTOR_ROUTE_AB=false）。
    # [2026-09-19 撤回误塞] `trend_chart_review`（多模态图审）属于**学习进化区**，已从主策略画布移除；
    # 它的设计见上述文档 G6。这里删除节点，不留下"看着在跑但不知属于哪块"的孤儿。
    {"id": "midlong_executor", "group": "G4", "size": "L", "label": "中线执行（tier=mid）",
     "role": "**中线**新开唯一 Writer（authority=llm_thesis）：读论题库 → 决定开/拒 → 写审计漏斗",
     "cadence_label": "随 tick",
     # [轮124 2026-09-19 拆车道] 中/长线是**同一个 Writer**（`execute_midlong_open`），
     # 此前只有一张"中线执行"卡、且混着两条车道的行（实测 `tier=mid`=0、`tier=long`=1，
     # 因为执行日志**根本没带 tier**；同一轮已给日志补上 tier）⇒ 长线执行在画布上无处可见
     # —— 这正是用户问的"怎么没有长线执行"。现在按 tier 拆成两张对称的卡。
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\[MidLong\](?!.*tier=long)"},
     "deps": ["thesis_store"]},
    {"id": "midlong_exec", "group": "G4", "size": "L", "label": "长线执行（execute_midlong_open · tier=long）",
     "role": "**长线**新开：与中线共用同一 Writer（`execute_midlong_open`），按 tier 分卡显示；"
             "长线另有独立通道 `trend_e1_engine`（cron 直连 place_order，绕过 Single Writer）",
     "cadence_label": "随 tick",
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\[MidLong\].*tier=long"},
     "deps": ["thesis_store"]},
    {"id": "mid_position_mgr", "group": "G4", "size": "M", "label": "中线持仓管理（midlong_position_manager）",
     "role": "中线持仓的读取/合并/减仓与退出触发（`_open_midlong_positions` 等）；"
             "长线对应物是 `trend_agent.review_position`——**两条车道各有自己的持仓层**",
     "cadence_label": "随 tick",
     "source": {"kind": "file", "path": "logs/backend.log",
                "filter": r"midlong_position_manager|MidLongPositionManager"},
     "deps": ["midlong_executor"]},
    {"id": "mid_exit", "group": "G4", "size": "M", "label": "中线出场（ExitPolicy · 与长线参数已分离）",
     "role": "出场唯一权威 `ExitPolicy`（轮100/101 已把中/长线参数拆开：止损来源、复查节奏、"
             "TP 分档各自独立）。长线走 Chandelier + 滚仓，中线走 ExitPolicy 分档止盈——"
             "**这正是「中线与长线最后完全不同」的落点之一**",
     "cadence_label": "事件驱动",
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"ExitPolicy|exit_policy"},
     "deps": ["mid_position_mgr"]},
    {"id": "direction_audit", "group": "G4", "size": "M", "label": "中线决策审计漏斗",
     "role": "中/长线决策漏斗审计（jsonl，无 API）",
     "cadence_label": "事件驱动",
     "source": {"kind": "file", "path": "data/midlong_direction_audit.jsonl", "tail_bytes": 32768},
     "deps": ["midlong_executor"]},
    {"id": "trend_agent", "group": "G4", "size": "M", "label": "长线持仓复核（trend_agent）",
     "role": "**活路径**：review_position / evaluate_pyramid（持仓复核与加仓，由 coordinator_loop 触发）；"
             "**死路径**：analyze_direction（mlto_cycle.py:469-472 提前 return → 'brain_owns_entry'，恒不可达）",
     "cadence_label": "名义 5400s（实测 7~15 分钟，见审计）", "expected_interval_s": 5400,
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"TrendAgent"},
     "deps": ["coordinator"]},
    # [轮128 2026-09-20 用户指令] 「旧 swing 模块（非中线！）」节点**已删除**：
    #   它 source=kind:none（0 行、边=断链），本质是一个**代码模块**而不是画面上的 agent，
    #   画出来只会让人问「这是怎么回事」。退役事实改由 `retired_layers` 说明（同 QAA 的处理）。
    #   删除前把该卡原来的说法逐条核实了一遍（原文本称有 4 处"仍在使用"）：
    #     · `_archive_prompt`              → **活**（trend_agent.py:389-390 复用做 prompt 落盘）
    #     · `swing_agent.update_thesis`    → 死路径（唯一调用者 orchestrator 已于 09-05 下线）
    #     · master_execution.py 的 3 处 import → 死导入（AST：名字从未被使用，本轮已删）
    #     · `is_swing_nature` / `derive_swing_side` → 0 个生产调用点（仅注释里被提到）
    #   取证：scripts/_probe128_swing_residuals.py + _probe128g_calltype.py（call_type 全表 swing=0）
    #   防回退：backend/tests/unit/test_agent_wall_retired_swing_20260920.py
    {"id": "scalp_lane", "group": "G4", "size": "S", "label": "短线（已关停）",
     "role": "SCALP_OPEN_DISABLED=true",
     "cadence_label": "已关停", "status_hint": "disabled",
     "source": {"kind": "none", "reason": "SCALP_OPEN_DISABLED=true：orchestrator 不注册 scalp 循环"},
     "deps": []},
    {"id": "position_sizing", "group": "G4", "size": "S", "label": "仓位计算",
     "role": "确定性仓位数学层（3 处生产调用点）",
     "cadence_label": "事件驱动",
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\[SizingAgent\]"},
     "deps": ["midlong_executor"]},
]

# ─────────────────────────── 边（关系） ───────────────────────────
EDGES: List[Dict[str, Any]] = [
    {"from": "data_center", "to": "kline_collector", "kind": "data", "label": "行情"},
    {"from": "kline_collector", "to": "factor_engine", "kind": "data", "label": "K线"},
    {"from": "kline_collector", "to": "coin_select", "kind": "data", "label": "K线"},
    {"from": "factor_engine", "to": "brain_mid", "kind": "data", "label": "因子"},
    {"from": "factor_engine", "to": "brain_long", "kind": "data", "label": "因子"},
    # [轮125 2026-09-19 补关联] 上一轮的补边锚点没匹配、一条都没进图（用户："关联关系没有"）：
    # 长线执行与中线执行是**同一个 Writer 的两个 tier**，E1 是长线另一条独立通道，
    # 持仓复核(revtrend_agent 的 review_position)挂在长线执行之后。
    {"from": "thesis_store", "to": "midlong_exec", "kind": "data", "label": "长线论题"},
    {"from": "midlong_loop", "to": "midlong_exec", "kind": "trigger", "label": "tick(tier=long)"},
    {"from": "midlong_exec", "to": "trend_agent", "kind": "trigger", "label": "持仓复核/加仓"},
    {"from": "data_center", "to": "trend_e1_engine", "kind": "data", "label": "日线行情"},
    {"from": "trend_e1_engine", "to": "position_sizing", "kind": "trigger", "label": "E1 直接下单"},
    {"from": "coin_select", "to": "midlong_exec", "kind": "data", "label": "AI 长线候选"},
    # [轮126 车道对称] 中线链：主脑 → 执行 → 持仓管理 → 出场 → 审计（与长线链同形）
    {"from": "coin_select", "to": "brain_mid", "kind": "data", "label": "中线候选池"},
    {"from": "brain_mid", "to": "midlong_executor", "kind": "trigger", "label": "开仓决策(tier=mid)"},
    {"from": "midlong_executor", "to": "mid_position_mgr", "kind": "data", "label": "持仓管理"},
    {"from": "mid_position_mgr", "to": "mid_exit", "kind": "trigger", "label": "退出触发"},
    {"from": "mid_exit", "to": "direction_audit", "kind": "audit", "label": "出场审计"},
    {"from": "midlong_exec", "to": "direction_audit", "kind": "audit", "label": "开仓审计(long)"},
    {"from": "coin_select", "to": "brain_mid", "kind": "data", "label": "候选池"},
    # [2026-09-19 修正] 中/长线的真实触发链：`midlong_loop`（同一循环的两个 tier）
    # → 派发 mid/long 主脑子进程；→ 触发 trend_agent 持仓复核。
    # 此前画的 `coordinator → brain_*`、`coordinator → trend_agent` **实测 0 穿透，全部删掉**。
    {"from": "midlong_loop", "to": "brain_mid", "kind": "trigger", "label": "派发 mid tier"},
    {"from": "midlong_loop", "to": "brain_long", "kind": "trigger", "label": "派发 long tier"},
    {"from": "midlong_loop", "to": "trend_agent", "kind": "trigger", "label": "持仓复核(review/pyramid)"},
    {"from": "midlong_loop", "to": "midlong_executor", "kind": "trigger", "label": "执行派发"},
    {"from": "brain_mid", "to": "thesis_store", "kind": "decision", "label": "论题写入"},
    {"from": "brain_long", "to": "thesis_store", "kind": "decision", "label": "论题写入"},
    {"from": "thesis_store", "to": "midlong_executor", "kind": "decision", "label": "开仓权威"},
    # [2026-09-19 修复] 原 `coordinator → trend_agent` 实测 **0 次穿透**
    # （`[TrendAgent] 复查` 0 命中、`持仓时限AI复审开始` 0 命中）⇒ 已删除，改由 midlong_loop 触发（见上）。
    # 长线 E1 开仓：读长线提案，可拒（逐笔拒绝率未统计）。
    {"from": "thesis_store", "to": "trend_e1_engine", "kind": "decision", "label": "长线提案(可被拒)"},
    # 中线因子路线：已证实会成交（近 24h 中线 9 笔中 2 笔 entry_source=factor_route）。
    {"from": "midlong_executor", "to": "direction_audit", "kind": "audit", "label": "审计落盘"},
    {"from": "midlong_executor", "to": "position_sizing", "kind": "decision", "label": "仓位"},
    # 观察型 → 预测账本 → 评分 → 可信度门（observe：建议不生效 —— 画灰虚线）
    {"from": "anomaly", "to": "signal_review", "kind": "ledger", "label": "预测账本"},
    {"from": "signal_review", "to": "experiment_advance", "kind": "advice", "label": "建议(observe 未生效)", "ineffective": True},
    {"from": "event_impact", "to": "experiment_advance", "kind": "advice", "label": "建议(observe 未生效)", "ineffective": True},
    {"from": "execution_qa", "to": "experiment_advance", "kind": "advice", "label": "建议(observe 未生效)", "ineffective": True},
    {"from": "experiment_advance", "to": "midlong_executor", "kind": "config", "label": "采纳后生效", "ineffective": True},
]

#: 结构性死链（2026-09-19 实测，每条带证据；不是现场计算出来的，故单独标注 kind）
STATIC_FINDINGS: List[Dict[str, Any]] = [
    # [2026-09-19] 原 `qaa_never_registered` 条目已移除：QAA v3 卡片编排层**已正式退役**
    # （不是"故障待修"），画布不再列它；退役事实改由 `retired_layers` 说明（见 ADR）。
    {"id": "experiments_empty", "severity": "high", "nodes": ["experiment_advance"],
     "what": "experiments 表 0 行，experiment_advance 已空转 600+ 次",
     "evidence": "base.py:276-278 observe 模式不落卡 + 6 个 agent 全 observe；job_registry run_count=623",
     "kind": "static_verified", "measured_at": "2026-09-19"},
    {"id": "timing_stale_no_gate", "severity": "high", "nodes": ["timing"],
     "what": "timing 停用 16 天，但 /api/agents/timing/weights 仍 200 返回旧值且无新鲜度门；capital_allocator 仍消费它",
     "evidence": "jobs.py:268-269 停注册；产物 mtime 2026-09-03；allocation/capital_allocator.py:217 读它",
     "kind": "static_verified", "measured_at": "2026-09-19"},
    {"id": "long_tick_dead_config", "severity": "medium", "nodes": ["brain_long"],
     "what": "TIER_LONG_AI_TICK_SEC=240 是死配置：实测 long 间隔 p50=182s（与 mid 同频）",
     "evidence": "mark_tier_run 唯一调用点 coordinator_loop.py:115 只标 short ⇒ mid/long 恒 due；tier_tick_scheduler.get_due_ai_tiers 每轮返回 ['mid','long']",
     "kind": "static_verified", "measured_at": "2026-09-19"},
    {"id": "trend_chart_overclock", "severity": "medium", "nodes": [],
     "what": "analysis_trend_chart 声明 28800s（8h），实测 7~17 分钟（最高 11 倍超频）",
     "evidence": "scheduler.py:187-192 每次注册把相位重置为 now+60~120s，且 replace_existing=True；09-19 后端启动 40 次",
     "kind": "static_verified", "measured_at": "2026-09-19"},
    {"id": "restart_storm", "severity": "high", "nodes": [],
     "what": "后端 2026-09-19 启动 40 次（gap 中位 691s），无优雅退出痕迹；interval 任务相位反复重置",
     "evidence": "backend.log* 'Started server process' 40 次；'Shutting down'/'SIGTERM' 0 命中；backend-watchdog 0 restart",
     "kind": "static_verified", "measured_at": "2026-09-19"},
    {"id": "fixed_by_this_round", "severity": "info", "nodes": ["execution_qa"],
     "what": "execution_qa advise 的 KeyError('avg_bp') 已修（原先每日 07:15 必崩、且被 ok=true 掩盖）",
     "evidence": "backend/services/agents/execution_qa.py + base.py（本轮修复，测试 test_agent_text_visibility_bugs_20260919.py）",
     "kind": "fixed", "measured_at": "2026-09-19"},
]


# ─────────────────────────── 事实采集 ───────────────────────────

def _jobs_by_name() -> Dict[str, Dict[str, Any]]:
    """读 job_registry（**复用其 stale 判定**，不另算）。失败返回空 dict（不抛）。"""
    try:
        from backend.services.ops.job_registry import list_jobs

        return {str(j.get("name")): j for j in (list_jobs() or [])}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AgentWall] job_registry 不可用: %s", exc)
        return {}


def _latest_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    p = DATA_AGENTS / f"latest_{agent_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _artifact_age_s(path: Path) -> Optional[float]:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except Exception:  # noqa: BLE001
        return None


def _observe_mode(agent_id: str) -> Optional[str]:
    d = _latest_agent(agent_id)
    return (d or {}).get("mode")


def _retired_layers_note() -> List[Dict[str, str]]:
    """已退役、因此**不在画布列出**的层（避免"为什么没有 X"重复发问）。

    [轮128 2026-09-20] 原函数名 `_qaa_retired_note` 只覆盖 QAA 一项；现已含 swing_agent，
    故改名（旧名不再保留别名——全库无其它调用点，见 test_agent_wall_retired_swing_20260920.py）。
    """
    return [{
        "id": "qaa_v3_cards",
        "since": "2026-09-17",
        "doc": "docs/ADR_QAA退役_20260919.md",
        "note": "QAA v3 卡片编排层（9 张卡 + EventBus 调度）已正式退役；能力由 ai_first 统一循环 + 主脑 MLTO + 观察型 Agent 群承接",
    }, {
        # [轮128 2026-09-20 用户指令] 用户看着这张 0 行/断链卡问「这是怎么回事，有用么，没用删掉」。
        # 结论：**模块不删（有 1 个活引用），卡删**——它不是 agent（无调度、无产物、无 job_registry 行）。
        # 实测（AST + 运行时审计）：唯一活引用是 `_archive_prompt`（trend_agent.py:389-390）；
        # `swing_agent.update_thesis` 因唯一调用者 orchestrator 于 09-05 下线而成为死路径；
        # master_execution.py 里 3 处 import 是死导入（本轮已删）；
        # `is_swing_nature`/`derive_swing_side` 0 个生产调用点（原卡片称"仍在使用"，与代码不符）。
        # 中线车道真实承担者 = mlto/brain.py 的 model_gateway（call_type=sync:analysis.model_gateway）。
        "id": "swing_agent_v1",
        "since": "2026-09-20",
        "doc": "backend/services/swing_agent.py:26-51（模块自述+实测表）+ scripts/_probe128_swing_residuals.py",
        "note": ("旧 swing 独立分析路径已并入长线 thesis 的 mid_view（Phase 4）；画布不再列它。"
                 "文件保留仅因 `_archive_prompt` 仍被 trend_agent 复用（唯一活引用，trend_agent.py:389-390）；"
                 "其余引用经实测为死路径/死导入（详见模块 docstring 的核实表）。"
                 "中线车道由 brain_mid + midlong_executor 承担，mid 论题 LLM 走 model_gateway"),
    }]


def _status_for_node(node: Dict[str, Any], jobs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """节点状态：优先 job_registry 的 stale 判定，其次产物 age，最后静态 hint。"""
    out: Dict[str, Any] = {"status": "unknown", "reason": "", "last_success_ms": None,
                           "run_count": None, "declared_interval_s": node.get("expected_interval_s")}
    job = jobs.get(str(node.get("job") or ""))
    if job:
        stale = job.get("stale")
        out["run_count"] = job.get("run_count")
        out["declared_interval_s"] = job.get("expected_interval_sec") or out["declared_interval_s"]
        out["cadence"] = job.get("cadence")
        for key in ("last_end", "last_start", "heartbeat_at"):
            v = job.get(key)
            if v:
                out["last_success_ms"] = v
                break
        mapping = {None: ("ok", ""), "ok": ("ok", ""), "warn": ("stale", "超过期望间隔 2 倍"),
                   "critical": ("dead", "超过期望间隔 5 倍以上"), "never_ran": ("never", "登记后从未运行")}
        st, why = mapping.get(stale, ("unknown", f"job_registry.stale={stale}"))
        out["status"], out["reason"] = st, why
        if job.get("enabled") is False:
            out["status"], out["reason"] = "disabled", "job_registry.enabled=false"
        # 结构性停用优先于"从未运行"：否则画布会把"已停用"显示成"从未运行"，两者含义不同
        # （前者是决策，后者是故障）。两者都写进 reason，避免丢信息。
        if node.get("status_hint") == "disabled":
            out["status"] = "disabled"
            out["reason"] = f"已停用（{node.get('cadence_label')}）；job_registry 残留判定={stale}"
        return out

    src = node.get("source") or {}
    if src.get("kind") == "json_latest":
        d = _latest_agent(str(src.get("agent")))
        if d:
            out["last_success_ms"] = d.get("ts_ms")
            out["status"] = "ok"
            out["reason"] = "按产物 mtime/ts_ms 判定（该节点无 job_registry 行）"
            return out
        out["status"], out["reason"] = "never", "无 latest 产物"
        return out

    if src.get("kind") == "json_thesis":
        rows = _thesis_lines(str(src.get("session") or ""), limit=1)
        if rows:
            out["status"] = "ok"
            out["reason"] = f"论题库有数据：{rows[0]['text'][:60]}"
            return out
        out["status"], out["reason"] = "unknown", "该会话论题库为空或 analytics 库不可达"
        return out

    hint = node.get("status_hint")
    if hint:
        out["status"] = {"disabled": "disabled", "static_dead": "dead"}.get(hint, "unknown")
        out["reason"] = "结构性事实（见审计依据）"
        return out

    fp = src.get("path")
    if fp:
        age = _artifact_age_s((ROOT / fp) if not str(fp).startswith("logs/") else (ROOT / fp))
        if age is not None:
            out["status"] = "ok"
            out["reason"] = f"文件 mtime {int(age)}s 前（无 job_registry 行）"
            out["last_success_ms"] = int((time.time() - age) * 1000)
            return out
    return out


def _jobs_or_empty() -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    """取 job_registry；失败时**降级但显式标记**（返回告警文本，不静默、也不整页崩）。"""
    try:
        return _jobs_by_name(), None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AgentWall] job_registry 读取失败，状态降级为 unknown: %s", exc)
        return {}, f"job_registry 不可用：{type(exc).__name__}: {exc}"


def build_state() -> Dict[str, Any]:
    """画布骨架：节点 + 边 + 分组摘要。**单次请求拿全**。"""
    jobs, jobs_warn = _jobs_or_empty()
    nodes: List[Dict[str, Any]] = []
    groups: Dict[str, Dict[str, Any]] = {}
    for n in NODES:
        node = dict(n)
        node["status_detail"] = _status_for_node(n, jobs)
        node["status"] = node["status_detail"]["status"]
        node["observe_mode"] = _observe_mode(str((n.get("source") or {}).get("agent") or ""))
        nodes.append(node)
        g = groups.setdefault(node["group"], {"group": node["group"], "total": 0,
                                              "ok": 0, "stale": 0, "dead": 0, "never": 0,
                                              "disabled": 0, "unknown": 0})
        g["total"] += 1
        st = node["status"] if node["status"] in g else "unknown"
        g[st] += 1

    node_ids = {n["id"] for n in nodes}
    edges = []
    for e in EDGES:
        if e["from"] not in node_ids or e["to"] not in node_ids:
            continue          # 禁止画悬空边（宁可少画一条，也不画假边）
        src = next(n for n in nodes if n["id"] == e["from"])
        dst = next(n for n in nodes if n["id"] == e["to"])
        health = _edge_health(e, src, dst)
        edges.append({**e, "health": health})

    return {
        "generated_at_ms": int(time.time() * 1000),
        "groups": [groups[k] for k in sorted(groups)],
        "nodes": nodes,
        "edges": edges,
        "retired_layers": _retired_layers_note(),
        "warnings": [jobs_warn] if jobs_warn else [],
        "note": ("节点状态复用 job_registry 的 stale 判定（None→ok / warn→stale / critical→dead / "
                 "never_ran→never）；dead/disabled 为结构性事实，依据见 /api/agent-wall/audit"),
    }


def _edge_health(edge: Dict[str, Any], src: Dict[str, Any], dst: Dict[str, Any]) -> Dict[str, Any]:
    """边健康四态：ok / stale / broken / ineffective（未生效）。"""
    if edge.get("ineffective"):
        return {"state": "ineffective", "reason": "链路存在但被开关或 observe 模式短路（建议已记录、不生效）"}
    if src["status"] in ("never", "dead"):
        return {"state": "broken", "reason": f"生产端 {src['id']} 状态={src['status']}"}
    if src["status"] == "stale" or dst["status"] in ("stale", "never", "dead"):
        return {"state": "stale", "reason": f"生产端={src['status']} 消费端={dst['status']}"}
    return {"state": "ok", "reason": ""}


# ─────────────────────────── 增量流（游标） ───────────────────────────

_LINE_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _read_incremental(node_id: str, path: Path, filt: Optional[str],
                      max_lines: int = MAX_LINES_PER_NODE) -> List[Dict[str, Any]]:
    """增量读；首次读做**有界反向扫描**（从尾部往前找命中行），之后按 offset 追加。

    为什么不直接"从尾部 64KB 读一次"：`logs/brain_subprocess.log`（146MB）尾部窗口里
    可能一条 `tier=mid` 都没有 ⇒ 节点滚屏空白（实测发生过）。反向扫描上限 `_SCAN_BUDGET`
    （默认 4MB），保证单次请求有界。
    """
    if not path.exists():
        return []
    key = f"{node_id}:{path}"
    try:
        st = path.stat()
    except Exception:  # noqa: BLE001
        return []
    offset, ino = _OFFSETS.get(key, (None, st.st_ino))
    if ino != st.st_ino or (offset is not None and st.st_size < offset):
        offset = None                                   # 轮转/截断 ⇒ 重新定位

    pat = re.compile(filt) if filt else None

    # ── 首读：有界反向扫描，凑够 max_lines 条命中 ──
    if offset is None:
        budget = min(_SCAN_BUDGET, st.st_size)
        chunk = 256 * 1024
        collected: List[str] = []
        pos = st.st_size
        while pos > 0 and budget > 0 and len(collected) < max_lines:
            size = min(chunk, pos)
            pos -= size
            budget -= size
            try:
                with path.open("rb") as fh:
                    fh.seek(pos)
                    blob = fh.read(size)
            except Exception:  # noqa: BLE001
                break
            text = blob.decode("utf-8", errors="replace")
            parts = text.splitlines()
            if pos > 0 and parts:
                parts = parts[1:]                        # 首行可能被截断，丢弃
            hits = [ln for ln in parts
                    if (not pat or pat.search(ln)) and not _is_access_noise(ln)]
            collected = hits + collected
        _OFFSETS[key] = (st.st_size, st.st_ino)
        out = [_fmt_line(ln) for ln in collected[-max_lines:]]
        return out

    if st.st_size == offset:
        return []
    out = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            for raw in fh:
                line = raw.rstrip("\n")
                if not line or (pat and not pat.search(line)):
                    continue
                out.append(_fmt_line(line))
                if len(out) >= max_lines:
                    break
            _OFFSETS[key] = (fh.tell(), st.st_ino)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AgentWall] 读 %s 失败: %s", path, exc)
        return []
    return out


def _fmt_line(line: str) -> Dict[str, Any]:
    m = _LINE_RE.match(line)
    return {"ts": m.group("ts") if m else "", "text": to_zh(line), "raw": line[:600]}


# ── 中文自然语言编译（"分析都是英文"的直接修复） ──
# 画布上的节点滚屏原来直接吐原始日志行（英文技术串，如 `[BrainSubprocess] 启动 tier=mid symbols=12`），
# 用户看不懂、也无法"一眼看出这个 agent 在做什么"。这里把**已知的日志形态**编译成中文叙述；
# 未命中的行原样保留（不编造），并在 `raw` 字段里始终保留原文以便核对。
_ZH_RULES: List[Tuple[re.Pattern, str]] = [
    # ── 主脑（中/长线）──
    (re.compile(r"\[BrainSubprocess\] 启动 tier=(\w+) symbols=(\d+) trigger=(\w+)"),
     r"主脑开始一轮分析｜车道=\1 标的数=\2 触发=\3"),
    (re.compile(r"\[BrainSubprocess\] 完成 tier=(\w+) n=(\d+)"),
     r"主脑完成一轮｜车道=\1 产出论题=\2 条"),
    (re.compile(r"\[MidLongBrain\] batch tier=(\w+) n=(\d+) watch=(\d+) idle=(\d+) opened=(\d+)"
                r"(?: priority=\[(.*?)\])?(?: pass=(\d+))?"),
     r"主脑批次｜车道=\1 标的=\2 观察=\3 空转=\4 开仓=\5 优先=\6 通过=\7"),
    (re.compile(r"\[MidLongBrain\] 开仓扫描 tier=(\w+) 候选=(\d+) 成交=(\d+)"),
     r"中线开仓扫描｜候选=\2 实际成交=\3"),
    (re.compile(r"\[MidLongBrain\]"),
     r"主脑（中线）｜"),
    # ── 中线执行 ──
    (re.compile(r"\[MidLong\] stage=(\w+) symbol=(\w+) authority=(\w+) source=(\w+) action=(\w+) reason=([^|]+)"),
     r"中线执行｜\2 阶段=\1 权威=\3 来源=\4 动作=\5 理由=\6"),
    (re.compile(r"\[MidLong\] stage=(\w+) symbol=(\w+) authority=(\w+)"),
     r"中线执行｜\2 阶段=\1 权威=\3"),
    (re.compile(r"\[MidLong\] stage=(\w+) symbol=(\w+)"),
     r"中线执行｜\2 阶段=\1"),
    (re.compile(r"\[MidLong\]"), r"中线执行｜"),
    (re.compile(r"位置闸|缩仓×"), r"风控闸："),
    # ── 中线审计 ──
    (re.compile(r"\[MidLongAudit\] (skip|open_attempt|opened) stage=(\w+) symbol=(\w+) reason=([^|]+)"),
     r"中线决策审计｜\3 阶段=\2 结论=\1 原因=\4"),
    (re.compile(r"\[MidLongAudit\] (\w+) stage=(\w+) symbol=(\w+)"),
     r"中线决策审计｜\3 阶段=\2 结论=\1"),
    (re.compile(r"\[MidLongAudit\]"), r"中线决策审计｜"),
    # ── 长线 ──
    (re.compile(r"\[TrendAgent:review\]"), r"长线持仓复核｜定期复核"),
    (re.compile(r"\[TrendAgent:pyramid\]"), r"长线持仓复核｜金字塔加仓评估"),
    (re.compile(r"\[TrendAgent:direction\]"), r"长线方向判断（该路径恒不可达）"),
    (re.compile(r"\[TrendAgent:(\w+)\]"), r"长线持仓复核｜\1"),
    # ── 其它常见 ──
    (re.compile(r"\[FactorRouteAB\] (\w+) action=(\w+) score=([-\d.]+)"),
     r"因子路线A/B｜\1 动作=\2 得分=\3"),
    (re.compile(r"\[FactorRouteAB\]"), r"因子路线A/B｜"),
    (re.compile(r"\[AutoCoinSelector\]"), r"AI 选币｜"),
    (re.compile(r"\[FreshnessWatch\]"), r"K线新鲜度守卫｜"),
    (re.compile(r"\[GILWatch\]"), r"GIL/排队观测｜"),
    (re.compile(r"\[DB LeakGuard\]"), r"DB 泄漏守卫（idle-in-transaction）｜"),
    (re.compile(r"\[Agent:(\w+)\] (\w+) 失败: (.*)"),
     r"观察型 Agent｜\1 阶段=\2 失败：\3"),
    (re.compile(r"\[Agent:(\w+)\]"), r"观察型 Agent｜\1"),
]


# [轮124 2026-09-19] 访问日志噪音：`uvicorn.access` / `"GET /api/...` 这类行**永远不是**
# agent 活动，却会命中"按 URL 关键词过滤"的卡片（实测长线卡 124 行全是它自己的轮询）。
# 在取行处统一剔除，比逐卡改过滤词更可靠（新增卡片不会再踩同一个坑）。
_ACCESS_NOISE_RE = re.compile(r"uvicorn\.access|\"\s*(?:GET|POST|PUT|DELETE|PATCH) /api/|^INFO:\s+\d")


def _is_access_noise(line: str) -> bool:
    try:
        return bool(_ACCESS_NOISE_RE.search(line or ""))
    except Exception:
        return False


_ENVELOPE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]?\d*\s*\[[A-Z]+\]\s*(?:\[tr=[^\]]*\]\s*)?[\w\.]+(?::\d+)?\s*-\s*"
)


_ENVELOPE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,.]?\d*\s*\[[A-Z]+\]\s*(?:\[tr=[^\]]*\]\s*)?[\w\.]+:\d+\s*-\s*")


def to_zh(line: str) -> str:
    """把已知形态的日志行编译成中文叙述；未命中则**剥离日志外壳后原样返回**（不编造）。

    剥离外壳（`时间 [LEVEL] 模块:行 - `）的原因：画布左侧已单独显示时间，
    模块名对"看这个 agent 在做什么"是噪音；原文仍保留在 `raw` 字段供核对。
    """
    body = _ENVELOPE_RE.sub("", line)
    for pat, repl in _ZH_RULES:
        if pat.search(body):
            return pat.sub(repl, body, count=1).strip()
    return body.strip()


def _node_lines(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    src = node.get("source") or {}
    kind = src.get("kind")
    if kind == "file":
        rel = str(src.get("path") or "")
        base = LOGS if rel.startswith("logs/") else ROOT
        path = base / rel.split("/", 1)[1] if rel.startswith("logs/") else ROOT / rel
        return _read_incremental(node["id"], path, src.get("filter"))
    if kind == "json_file":
        # [轮124] 读**运行产物 JSON** 并按字段分行（E1 这类"每天跑一次"的节点，
        # 日志里在非运行时段本来就没有业务行，只能看最近一次运行结果）。
        rel = str(src.get("path") or "")
        p = (LOGS / rel.split("/", 1)[1]) if rel.startswith("logs/") else (ROOT / rel)
        try:
            import json as _json

            _d = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
        _out: List[Dict[str, Any]] = []
        if isinstance(_d, dict):
            for _k in sorted(_d.keys()):
                _v = _d[_k]
                if _v in (None, "", [], {}):
                    continue
                if isinstance(_v, (dict, list)):
                    _v = _json.dumps(_v, ensure_ascii=False)[:240]
                _out.append({"ts": "", "text": f"{_k}｜{_v}", "raw": f"{_k}={_v}"})
        return _out[:25]
    if kind == "json_latest":
        d = _latest_agent(str(src.get("agent")))
        if not d:
            return []
        ts = ""
        ms = d.get("ts_ms")
        if ms:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))
        lines: List[Dict[str, Any]] = []
        findings = d.get("findings") or {}
        adv = d.get("advice") or []

        def _add(text: str) -> None:
            # 文本为中文叙述；raw 保留原文（JSON 产物本身即原文）——"可回溯"要求
            lines.append({"ts": ts, "text": text, "raw": text})

        _add(f"观察型 Agent｜{d.get('agent')} 本轮运行 ok={d.get('ok')} 模式={d.get('mode')} "
             f"错误={d.get('errors') or '无'}")
        if isinstance(findings, dict):
            brief = json.dumps({k: findings[k] for k in list(findings)[:6]}, ensure_ascii=False)
            _add(f"分析要点：{brief[:500]}")
        for a in (adv or [])[:5]:
            if isinstance(a, dict):
                _add(f"建议 {a.get('action')} → {a.get('target')} "
                     f"严重度={a.get('severity')} 是否生效={a.get('applied')} {a.get('apply_note') or ''}")
        return lines
    if kind == "json_thesis":
        return _thesis_lines(str(src.get("session") or ""))
    if kind == "none":
        return []
    return []


def _thesis_lines(session_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    """论题库增量：直接读 DB（只读 SELECT），按 updated_at 倒序取最近若干条。"""
    try:
        from sqlalchemy import text as _text

        from backend.database.connection import AnalyticsSessionLocal

        db = AnalyticsSessionLocal()
        try:
            rows = db.execute(_text(
                "SELECT symbol, tier, direction, updated_at, left(coalesce(thesis_summary,''), 300) "
                "FROM mlto_thesis WHERE session_id = :sid ORDER BY updated_at DESC LIMIT :lim"
            ), {"sid": session_id, "lim": limit}).fetchall()
        finally:
            db.close()
        return [{"ts": r[3].strftime("%Y-%m-%d %H:%M:%S") if r[3] else "",
                 "text": f"[{r[1]}/{r[0]}] dir={r[2]} {r[4]}"} for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[AgentWall] 论题读取失败: %s", exc)
        return []


def tail(node_ids: Iterable[str]) -> Dict[str, Any]:
    """多节点增量流：`{lines: {node: [...]}, counts: {...}}`。一次请求覆盖所有可见节点。"""
    wanted = {str(x) for x in node_ids if str(x)}
    by_id = {n["id"]: n for n in NODES}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for nid in wanted:
        node = by_id.get(nid)
        if not node:
            out[nid] = []
            continue
        try:
            out[nid] = _node_lines(node)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[AgentWall] tail(%s) 失败: %s", nid, exc)
            out[nid] = []
    return {"lines": out, "counts": {k: len(v) for k, v in out.items()},
            "requested": sorted(wanted)}


# ─────────────────────────── 审计 ───────────────────────────

#: [2026-09-20] 已**按决策停用**的任务：`job_registry.enabled=false`（机制：`ops/job_registry.py:173
#: set_enabled` 写库 → `:269 job_run(respect_enabled)` 跳过执行；watchdog 在 `:453` 已跳过 enabled=false，
#: 所以这里不是新口径，只是把同一事实如实呈现）。
#: 为什么必须单独成类：`list_jobs()` 对停用行**照样算 stale**，于是"我们自己关掉的任务"被现场判定报成
#: high —— 实测 17 条 findings 里 8 条属于此类，真故障被淹掉。**决策 ≠ 故障**，两者必须分开。
#: 每项都要写清来源（谁、按什么开关停的）；启停接口：POST /api/ops/jobs/<name>/enable|disable。
_STOPPED_JOBS: Dict[str, str] = {
    "agent_timing": "画布节点 timing 自 2026-09-05 起标 disabled（NODES.cadence_label）；该任务无独立 env 开关",
    "agent_param_search": ".env:1714 AGENT_PARAM_SEARCH_ENABLED=false（2026-09-03 首轮扫描无 edge）",
    "analysis_timing": ".env:1619 ANALYSIS_TIMING_ENABLED=false",
    "analysis_weekly_review": ".env:1618 ANALYSIS_WEEKLY_ENABLED=false",
    "capital_allocate": ".env:1769 ALLOCATOR_APPLY=false（allocation/jobs.py:29 跳过 capital_allocate 空转）",
    "e5_shadow_scan": ".env:1795 E5_SHADOW_ENABLED=false（strategies/event/jobs.py 跳过注册；三策略无 edge）",
    "e5_shadow_kpi": ".env:1795 E5_SHADOW_ENABLED=false（同上）",
    "experiment_advance": "experiments 表 0 行、空转 600+ 次；2026-09-19 22:15 按用户指令停用（本轮降噪）",
}
#: 恢复动作（写 `<name>` 而不是 {name}，避免被 f-string 当变量插值）
_ROLLBACK = "恢复：POST /api/ops/jobs/<name>/enable"


def audit() -> Dict[str, Any]:
    """断链与逻辑错误清单：现场检查 + 已核实的结构性缺陷。**任何上游异常都降级为一条 finding**。"""
    jobs, jobs_warn = _jobs_or_empty()
    live: List[Dict[str, Any]] = []
    if jobs_warn:
        live.append({
            "id": "job_registry_unavailable", "severity": "high",
            "what": "job_registry 读取失败，本次审计的现场判定部分不完整",
            "evidence": jobs_warn, "kind": "live",
            "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    # 1) job_registry 里的 never_ran / critical / warn（**现场判定**，不是静态清单）
    stopped: List[Dict[str, str]] = []
    unregistered: List[str] = []
    for name, j in sorted(jobs.items()):
        stale = j.get("stale")
        if stale in ("never_ran", "critical", "warn"):
            if j.get("enabled") is False:
                # 决策 ≠ 故障：停用行的 stale 是残留，不是故障（见 _STOPPED_JOBS 注释）
                why = _STOPPED_JOBS.get(name)
                if why:
                    stopped.append({"job": name, "stale": str(stale), "why": why})
                else:
                    unregistered.append(name)
                continue
            live.append({
                "id": f"job:{name}",
                "severity": {"never_ran": "high", "critical": "high", "warn": "medium"}[stale],
                "what": f"任务 {name} 状态={stale}（cadence={j.get('cadence')}）",
                "evidence": f"job_registry: enabled={j.get('enabled')} run_count={j.get('run_count')} "
                            f"last_start={j.get('last_start')} expected_interval_sec={j.get('expected_interval_sec')}",
                "kind": "live", "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
    if stopped:
        live.append({
            "id": "jobs_stopped_by_decision", "severity": "info",
            "what": f"{len(stopped)} 个任务为**已决策停用**（job_registry.enabled=false），不计入现场故障；"
                    f"停用行的 stale/never_ran 是残留值，别再当故障处理",
            "evidence": "；".join(f"{s['job']}（残留 stale={s['stale']}）← {s['why']}" for s in stopped)
                        + f"；{_ROLLBACK}",
            "kind": "live", "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    for name in unregistered:
        j = jobs.get(name) or {}
        live.append({
            "id": f"job_disabled_unregistered:{name}", "severity": "medium",
            "what": f"任务 {name} 已停用（enabled=false）但**没有登记停用来源** —— 无法判断是决策还是误关",
            "evidence": f"job_registry: run_count={j.get('run_count')} last_start={j.get('last_start')} "
                        f"stale={j.get('stale')}；请在 agent_wall._STOPPED_JOBS 补登来源与回滚（{_ROLLBACK}）",
            "kind": "live", "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    # 2) 在册但从未产出产物的 agent（现场）。**已停用节点除外**：停用节点没有产物是预期结果
    #    （例：param_search 被 AGENT_PARAM_SEARCH_ENABLED=false 关掉，报"没有 latest 产物"是假故障）
    for n in NODES:
        if n.get("status_hint") == "disabled":
            continue
        src = n.get("source") or {}
        if src.get("kind") == "json_latest":
            if _latest_agent(str(src.get("agent"))) is None:
                live.append({
                    "id": f"artifact:{n['id']}",
                    "severity": "high",
                    "what": f"{n['label']}（{n['id']}）登记在册但没有任何 latest 产物",
                    "evidence": f"缺少 {DATA_AGENTS / ('latest_' + str(src.get('agent')) + '.json')}",
                    "kind": "live", "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })

    # 3) 停用节点却仍可在 API 上取到旧值（现场：产物 age）
    stale_products: List[Dict[str, Any]] = []
    for n in NODES:
        if n.get("status_hint") != "disabled":
            continue
        src = n.get("source") or {}
        if src.get("kind") == "json_latest":
            p = DATA_AGENTS / f"latest_{src.get('agent')}.json"
            age = _artifact_age_s(p)
            if age is not None and age > 86400:
                stale_products.append({
                    "id": f"stale_product:{n['id']}",
                    "severity": "high",
                    "what": f"{n['label']} 已停用，但产物仍存在且被 API 返回（{int(age/86400)} 天未更新）",
                    "evidence": f"{p} mtime {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.stat().st_mtime))}；"
                                f"仍由 /api/agents/latest/{src.get('agent')} 与 /api/agents/timing/weights 返回",
                    "kind": "live", "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
    live.extend(stale_products)

    # 4) observe 模式：建议不生效（现场读 latest_*.json 的 mode）
    observe = [n["id"] for n in NODES
               if _observe_mode(str((n.get("source") or {}).get("agent") or "")) == "observe"]
    if observe:
        live.append({
            "id": "observe_mode", "severity": "medium",
            "what": f"{len(observe)} 个 Agent 处于 observe：建议被记录但永不生效（画布以灰虚线标注）",
            "evidence": f"latest_*.json 的 mode=observe：{', '.join(observe)}；"
                        f"base.py:276-278 在 observe 下不落实验卡",
            "kind": "live", "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })


    findings = live + STATIC_FINDINGS
    by_sev: Dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {
        "generated_at_ms": int(time.time() * 1000),
        "counts": by_sev,
        "findings": sorted(findings, key=lambda f: {"high": 0, "medium": 1, "low": 2, "info": 3}
                           .get(f["severity"], 9)),
        "note": ("kind=live 为现场判定（每次请求重算）；kind=static_verified 为 2026-09-19 排查实测的结构性缺陷"
                 "（带证据，非现场重算）；kind=fixed 为本轮已修"),
    }
