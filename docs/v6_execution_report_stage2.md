# v6 计划 阶段 2 执行报告（S2-1 ~ S2-13）

> 日期：2026-08-05 ｜ 状态：✅ 全部完成
> 范围：本地算力章节实机改写的阶段 2（S2-1~S2-12 实现 + S2-13 汇总）

## 一、执行摘要

| 指标 | 结果 |
|---|---|
| 任务完成 | 12/12（S2-1~S2-12） |
| 新增 DB 迁移 | 3 个（0013 regime_suggestion / 0014 factor_snapshot / 0015 wisdom_quality） |
| 核心单测 | 13 文件 228 passed + 周边 10 文件 163 passed（含修复 3 失败） |
| 全量 unit | 1499 passed + 91 failed + 1 collection error（91 失败全部定性为既有环境性） |
| 前端构建 | frontend-next tsc 零错误 + next build EXIT=0（25 路由含 /intelligent-learning） |

## 二、交付清单（S2-1 ~ S2-12）

| 任务 | 交付物 | 证据 |
|---|---|---|
| S2-1 | L2 订单簿重建层 `market_flow/l2_reconstructor.py` + `l2_orderbook_manager.py`；K线管道补 taker_buy_volume / bid_depth_top5 / ask_depth_top5 | 产物核验 |
| S2-2 | DataProvider 统一接口 `data/data_provider.py`（Coinglass 过渡）+ 行情/链上健康监控补齐 | 产物核验 |
| S2-3 | 因子补全：liquidation_heatmap / onchain_netflow / wick_protection / stablecoin_mint_burn | `factors/onchain/onchain_factors.py` + `factors/derivatives/derivatives_factors.py` |
| S2-4 | 报告卡落库：factor card JSON → `factor_evolution_log`（metrics.card）+ 数据质量入卡 | `factor_card.py` + `factor_evolution_loop.py` L579-598 |
| S2-5 | WFO 增强：滚动训练窗 OOS IC 序列输出（oos_ic_series）+ 日级循环替换静态切分 | `evolution/factor_wfo.py` |
| S2-6 | 灰度阶梯：ai_governed 权重档位 0.40（env 配置 + 决策日志对比） | `decision_hub.resolve_governed_weight` |
| S2-7 | regime 参数建议通道：DB 持久化（模型列 + 迁移 0013 + persist/读回 + 测试） | `mlto/regime_suggestion.py` |
| S2-8 | confidence 校准器：ai_decision_logs 拟合 conf→胜率曲线（PAVA）+ API + paper 回填 + maintenance | `calibration/ai_decision_calibrator.py`；27 新增 + 55 回归全绿 |
| S2-9 | 选币升级：因子 IC 加权（Spearman）+ 余弦相关性去重 + LLM 组合决策 + factor_snapshot 列/迁移 0014 | `coin_rank/ic_weights.py`；test_coin_ic_weights 全绿 |
| S2-10 | 学习三通道：WisdomTracker 净扣费+质量闸门+验证强度排序（迁移 0015）+ 参数域扩展 + QAA 调度统一 | `services/wisdom_tracker.py` / `evolution/param_domain_expander.py` / `services/qaa_scheduler.py` |
| S2-11 | 前端配套：后端 5 端点（wisdom-loop / param-domain / qaa-scheduler / decision-chain / coin-feedback）+ Hermes 生命周期面板 | `api/intelligent_learning_routes.py` L295-521；前端见下文移植说明 |
| S2-12 | MCTS 因子挖掘增强：UCT + 短板扩展 + FSA + CoE + 宏微分离 | `evolution/mcts_miner.py` |

## 三、测试回归统计

- 核心 13 文件（S2 直接产物）：**228 passed / 0 failed**
- 周边 10 文件（受影响模块）：**163 passed / 0 failed**
  - 本轮修复：`test_autocoin_feedback.py` 3 失败（S2-9a 取价路径改为 `_resolve_inject_price` 导致 mock 目标过时；FakeService 补 add_symbols + auto_coin_symbols + catalog patch），修复后 12/12 通过。
- 全量 unit（排除 1 个既有 import 收集错误文件 test_phase5）：**1499 passed + 91 failed**

### 91 个失败定性（全部既有环境性/历史遗留，不在 S2 改动路径内）

| 文件 | 失败 | 根因 |
|---|---|---|
| test_full_flow_integration | 28 | AUTO_COIN_COOLING_PERIOD 常量不存在 / exchange 检测断言 / DB 表缺失 |
| test_phase4_market_scan | 18 | strategy_hypothesis_generator 模块不存在 |
| test_phase0_changes | 15 | funding_factors 类名不存在（模块重构） |
| test_ws_redis_bridge | 7 | redis 包未安装 |
| test_comprehensive_functional | 5 | DRLPanel.tsx/drlApi.ts 缺失 + 因子引擎耗时超标 |
| test_autocoin_social | 3 | rank engine 启用后旧测试失效 |
| test_phase6_exchange | 3 | BaseExchangeClient 不存在 |
| test_phase2_arbitrage | 3 | 包 import 变化 |
| test_unify_leverage | 2 | tier cap 行为差异 |
| test_full_auto_execution | 2 | RiskGate 规则改版后旧断言 |
| test_design_skeletons | 2 | feature flags 默认状态 |
| test_admin_bootstrap | 1 | 默认角色 user vs admin |
| test_autocoin_scoring | 1 | rank engine 启用后旧测试失效 |
| test_phase7_drl | 1 | TradingEnv 不存在 |

另：test_learning_execution_chain 5 失败为既有（完整链路依赖 DB/服务接线，mock 下 trade_count_total=0）。

## 四、S2-11 前端目录纠正（本轮重要修复）

**问题**：S2-11 的 8 个组件最初落在已冻结的 `frontend/`（FROZEN.md，:5173 禁用）；正式前端 `frontend-next`（:5273）无任何对应组件/API 层/页面。

**移植产物（frontend-next/）**：

| 文件 | 说明 |
|---|---|
| `src/lib/intelligentLearningApi.ts` | 5 个 S2 端点函数 + 类型，统一走 apiRequest（自动 Bearer + 401 refresh） |
| `src/lib/hermesApi.ts` | maturity / health / schedule / patterns 4 函数 + 类型 |
| `src/components/operations/IlcUi.tsx` | SectionCard / StatCard / StatusBadge / RefreshButton / EmptyState / InfoBanner |
| `src/components/operations/WisdomLoopPanel.tsx` | 通道一：wisdom 闭环 |
| `src/components/operations/ParamDomainPanel.tsx` | 通道二：参数域扩展 |
| `src/components/operations/QaaSchedulerPanel.tsx` | 通道三：QAA 调度统一心跳 |
| `src/components/operations/DecisionChainPanel.tsx` | 决策链路视图 |
| `src/components/operations/CoinFeedbackPanel.tsx` | 选币反馈面板 |
| `src/components/operations/HermesLifecyclePanel.tsx` | Hermes 生命周期总览 |
| `src/app/intelligent-learning/page.tsx` | 四 Tab 页面（生命周期 / 学习三通道 / 决策链路 / 选币反馈） |
| `src/components/layout/Sidebar.tsx` | 「市场 & 分析」组新增「智能学习」导航 |

**验证**：tsc --noEmit 零错误；next build EXIT=0，25 路由全部静态生成（含 /intelligent-learning）。

## 五、关键决策与遗留建议

1. **S2-11 目录纠正**：后续前端改动一律落在 frontend-next/；冻结目录 frontend/ 中 S2-11 旧产物保留作参考。
2. **测试修复路径**：S2-9a 改取价路径后需同步 mock 目标（`_fetch_market_snapshot` → `_resolve_inject_price`），未来改内部实现时注意测试同步。
3. **遗留**：test_learning_execution_chain 5 失败与 91 个历史失败建议按模块排期更新测试（phase0/4/6/7 重构、RiskGate 规则改版、rank engine 启用、redis 依赖安装）。
4. **衔接**：S2-8 校准器、S2-9 IC 加权、S2-10 三通道可直接为阶段 3（决策闭环）/阶段 4（RL 挖掘）提供数据管道。
