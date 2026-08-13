# Alpha Arena · ARCHITECTURE

> 本文档为 **v3 整改（2026-04）完成版**的系统架构速览。
> 历史迭代性方案文档位于 `docs/SYSTEM_UPGRADE_DESIGN_V3.md` 与 `backup_pre_v3_upgrade/`，仅供对照。

---

## 1. 顶层视图

```
┌────────────────────────── Frontend (Next.js) ──────────────────────────┐
│  ATASv2 ·  AI Learning Center ·  Strategy Creator ·  PositionDetail   │
│        ▲ REST /api/*          ▲ WebSocket /ws/*          ▲ UI         │
└────────────────────────┬─────────────────────┬────────────────────────┘
                         │                     │
┌────────────────────────┼─────────────────────┼────────────────────────┐
│ FastAPI (backend/main.py)                                             │
│  ├─ api/*_routes.py  (REST)                                           │
│  ├─ api/ws.py + services/ws_broadcast.py  (server→client push)        │
│  └─ services/* (业务服务群)                                            │
│                                                                       │
│  Decision pipeline:                                                   │
│  ┌──────────────┐   ┌───────────────────────┐   ┌──────────────────┐ │
│  │ Market Data  ├──▶│ full_auto_trading_svc ├──▶│  Paper / Live    │ │
│  └──────────────┘   │  · LLM / Orchestrator │   │  Exec Engine     │ │
│                     │  · RiskGate 层        │   └──────────────────┘ │
│                     │  · TDI (Kelly/DRL)    │                        │
│                     │  · Rebound Gate       │                        │
│                     │  · BlockReport 汇聚   │                        │
│                     └───────────────────────┘                        │
│                                                                       │
│  Learning loop (3-system):                                            │
│  Evolution (GA) ──▶ live genome ──▶ execution ──▶ Unified Learning   │
│       ▲                                       ┌───────────┐          │
│       └──── SystemCoordinator ◀──────────────┤ DRL / Kelly│          │
│                                                └───────────┘          │
└────────────────────────────────────────────────────────────────────────┘
                         │
                    PostgreSQL (SQLAlchemy)
```

## 2. 关键模块与职责

| 模块 | 路径 | 职责 |
| --- | --- | --- |
| Full-auto 决策循环 | `backend/services/full_auto_trading_service.py` | 单体编排：LLM 提议 → 风控 → TDI → 下单/减仓/防守 |
| Deterministic Risk Gate | `backend/services/deterministic_risk_gate.py` | 确定性风控：max_side_margin、单笔 margin 上限、proposed order 预估 |
| Rebound Gate | `backend/services/reentry_cooldown.py` + FA 入口多处 | 减仓后禁止 pyramid/DCA，防"减仓-再建"死亡螺旋 |
| BlockReport 聚合 | `backend/services/block_report_aggregator.py` | 进程内阻断事件 Top-N，给 UI 解释"为何不开单" |
| Trading Decision Interface | `backend/services/tdi/*` | Kelly 上限、DRL shadow 建议、组合风险聚合 |
| Evolution | `backend/services/strategy_evolver.py`, `evolution_scheduler.py`, `genetic_optimizer.py` | 遗传 GA，支持 `seed_genome` & 紧急进化 |
| StrategyParamsRegistry | `backend/services/strategy_params_registry.py` | 唯一 genome 写入口，`apply_genome()` 原子化更新 |
| DRL | `backend/services/rl/*` | PPO 训练 + 推理，异步 task_id |
| Kelly | `backend/services/rl/kelly_*` | KellyPositionSizer + PortfolioRiskAggregator |
| SystemCoordinator | `backend/services/rl/system_coordinator.py` | 触发 trigger_evolution / trigger_drl_retrain / arbitrate_conflicts |
| WS Broadcast | `backend/services/ws_broadcast.py` + `api/ws.py` | DRL/Kelly/Evolution 事件推送 |
| AI Strategy Routes | `backend/api/ai_strategy_routes.py` | CRUD + tier 配额 + /stats/tier-distribution |
| System Control | `backend/api/system_control_routes.py` | shutdown/status + **feature-flags (GET/POST)** + **block-report-top** |

## 3. 风控与决策顺序

优先级：`Risk Gate > Kelly > DRL shadow > Evolution`

1. 数据健康 / Circuit Breaker / Defensive Mode
2. Fee threshold gate
3. **Deterministic Risk Gate** （覆盖 max_side_margin，替代旧 exposure_gate_block）
4. Rebound Gate（pyramid / dca 之前）
5. TDI.apply：Kelly 上限夹紧 → DRL shadow 记录 → Portfolio risk 检查
6. Place order

## 4. 学习闭环

```
┌──────────────┐  params   ┌──────────────┐ decisions ┌──────────────┐
│  Evolution   ├──────────▶│   Live FA    ├──────────▶│ UnifiedLearn │
│  (GA, lineage)│           │ + Risk+TDI   │           │  (outcomes)  │
└──────▲───────┘           └──────┬───────┘           └──────┬───────┘
       │ trigger / seed          │                           │
       │                         ▼                           ▼
┌──────┴───────┐          ┌──────────────┐            ┌──────────────┐
│ SystemCoord  │◀─────────┤ DRL / Kelly  │◀───────────┤ WS Broadcast │
└──────────────┘  feedback└──────────────┘ status     └──────────────┘
```

## 5. Feature Flags（运行时可切换）

来源：`backend/config/settings.py`，运行时通过 `/api/system/feature-flags` 覆盖（仅当前进程）。

| Flag | 默认 | 说明 |
| --- | --- | --- |
| `ENABLE_DRL_INTEGRATION` | false | DRL 进入主循环；shadow 仍可单独打开 |
| `ENABLE_KELLY_POSITION` | **true** | Kelly 作为上限夹紧 |
| `ENABLE_EVOLUTION_FEEDBACK` | false | 进化结果写入实盘 genome |
| `ENABLE_PORTFOLIO_RISK` | **true**（2026-08-13 实况，用户确认） | PortfolioRiskAggregator |
| `ENABLE_COORDINATOR` | **true** | SystemCoordinator 自动触发紧急进化/DRL 重训 |
| `DRL_SHADOW_MODE` | true | DRL 仅记录不执行 |

## 6. Tier 差异化

- `AIStrategy.timeframe_tier` 必填（`short / mid / long`），DB 级默认 `mid`。
- `TIER_DIVERSITY_QUOTA = {short:0.35, mid:0.35, long:0.30}`。
- `_CYCLE_SLOT_MAP` 已去"全 mid 中性回退"，`volatile/breakout→short`、`unknown→long`。
- UI：AI Learning Center → Overview → **TierDistributionCard**（配合 `/api/ai-strategies/stats/tier-distribution`）。

## 7. 可观测性

- WS channels: `drl`, `kelly`, `evolution`
- REST: `/api/rl/drl/performance`, `/api/rl/kelly/portfolio`, `/api/rl/coordinator/status`, `/api/evolution/history`
- UI: **SystemCoordinationBanner · DRLPerformanceChart · MultiSymbolKellyTable · CorrelationMatrixView · TierDistributionCard · BlockReportTop3 · FeatureFlagsPanel**

## 8. 后续治理（P3 延期）

- **full_auto_trading_service.py 物理拆分** 为 5 个子模块（decision / risk / execution / defensive / observability）
- **RiskGate 协议统一**：当前 DRG + Rebound + fee + defensive 属串行过滤器，将合入统一 `RiskPipeline` 接口
