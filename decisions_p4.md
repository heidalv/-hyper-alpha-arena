# P4 规划草稿 — 因子真实落地 + 提示词进化闭环修复

> **状态**：规划草稿（未实施）  
> **起草日期**：2026-04-23  
> **前置**：P2/P3 风控与决策协调已上线；本包专注「情报进 LLM」与「学习闭环真产出」两条薄弱链。

> **文档位置**：以仓库根目录本文件为准；`docs/research/decisions_p4.md` 仅保留跳转，避免重复编辑。

---

## 一、为什么要做 P4

上一轮审计结论（代码 + 本地 DB 实证）可压缩为两句话：

1. **因子**：有计算与缓存，但默认提示词模板不引用因子占位符；`composite_v3` 缓存里 `factor_count` / `signal_score` 长期为 `null`；融合否决链存在**使用前未初始化变量**的静态缺陷风险；`.env` 中的 `FACTOR_SIGNAL_ENABLED` **无代码读取点**。
2. **提示词进化**：`prompt_training_records` 中 `optimized_prompt_id` **全部为 NULL**（36/36），`prompt_templates` 仅 3 条系统内置模板、无 `created_by=prompt_evolution` 的新行；策略仍全部绑定 `master_prompt_template_id=1`。

若不做 P4，产品叙事上的「因子驱动」「自我进化」与实盘行为**持续脱节**，且难以用日志自证。

---

## 二、目标与非目标

### 2.1 目标（必须可验收）

| ID | 目标 | 验收方式（客观） |
|----|------|------------------|
| P4-F1 | LLM 在默认路径下能**稳定看到**与交易相关的因子/合成摘要（可控长度） | 抽样 `ai_decision_logs`：`prompt_snapshot` 非空比例上升；或新增结构化字段记录「因子段落 hash」 |
| P4-F2 | `atas_factor_cache` 中 `composite_v3` 的 JSON **不再长期 null 关键字段**（或明确降级为「未计算」状态而非静默 null） | DB 抽样 + 单测对序列化结构断言 |
| P4-F3 | 因子融合否决（若保留）在 Full Auto 路径上**不因 NameError 被静默吞掉** | 单测覆盖 + 短时 shadow 日志中可见 `fusion_verdicts` 非空时的否决次数 |
| P4-F4 | 提示词进化在触发后 **至少能间歇性** 产生 `optimized_prompt_id` 非空的记录，且 `AIStrategy.master_prompt_template_id` 可版本递增 | `prompt_templates` 出现 `created_by=prompt_evolution`；`prompt_training_records.status=evolved` 占比 > 0 |
| P4-F5 | `FACTOR_SIGNAL_ENABLED`（或替代名）**要么接入真实分支，要么从配置文档删除**，避免「假开关」 | 代码 grep 有读取点；或 `.env.example` 不再列出无效项 |

### 2.2 非目标（本阶段不做）

- 重做整套因子库或引入新第三方因子平台。
- 大规模重写 `unified_learning_service` / GA 回测进化（与「实盘 prompt 进化」区分开）。
- 替代 P3 的 Master 门控、ai_reverse 冷却；P4 与之**正交**，仅减少「无效情报」与「假学习」噪音。

---

## 三、与 P3 的协调建议

- **推荐**：P3 继续按 runbook 观察（shadow → enforce）；P4 开发可在分支并行，上线采用**独立 feature flag**，避免一次改太多无法归因。
- **合并发布策略**：先上 P4 的「观测 + bugfix」（F2/F3/F5），再上「模板注入」（F1），最后上「进化落地」（F4），降低模型行为突变风险。

---

## 四、工作流 A — 因子真实落地

### A1. 修复 `composite_v3` 产物语义

- **定位**：`full_auto_trading_service.py` 写入 `atas_factor_cache` 时 `factor_id="composite_v3"`（约 1279 行附近）；向上追踪 `FactorSignalGenerator` / 合成因子的返回值构造。
- **动作**：
  - 明确 `factor_count`、`signal_score` 的计算来源；若当前管线不产生，则改为**不写入 null**（省略键或写 `0` + `reason`）。
  - 为合成结果增加 `schema_version`，便于以后迁移。

### A2. 默认 Prompt 模板注入（最小改动）

- **定位**：`backend/config/prompt_templates.py`（DEFAULT / PRO / HYPERLIQUID）；`backend/services/ai_decision_service.py` 中 `context` 合并（约 1487–1541 行：`adaptive_context`、`strategy_context`）。
- **动作**：
  - 在模板中增加**短**占位符块（例如「因子摘要」≤ N 字符），避免 token 爆炸。
  - 与现有 `unified_data_pool.get_factors_summary` 对齐，避免重复矛盾叙事。

### A3. 融合否决链健壮性（静态 bug）

- **定位**：`full_auto_trading_service.py` 中 `_execute_ai_decisions`：`orch_directions` 使用早于赋值（审计报告：约 6073 vs 6088 行）。
- **动作**：将 `orch_directions = {}`（或真实填充）**提前到首次 `.get` 之前**；禁止裸 `except` 吞掉 `NameError`（至少拆出日志级别或单独捕获）。

### A4. `FACTOR_SIGNAL_ENABLED` 治理

- **选项 1（推荐）**：在 `settings.py` 定义布尔项，在「写缓存 / 注入 prompt / 融合」三处统一短路。
- **选项 2**：删除环境变量与文档表述，避免误导运维。

---

## 五、工作流 B — 提示词进化闭环修复

### B1. 根因分层（实施前 30 分钟必做）

按优先级排查 `_call_llm_for_prompt_evolution`（`strategy_learning_service.py` 约 805–821 行）失败原因：

1. API / 鉴权 / 限流 → 日志中应有 `[Learning] LLM 调用失败`。
2. `generate_with_conversation` 返回非 `str` → 当前代码直接 `None`。
3. 返回长度 < 200 → 当前一律记 `llm_failed`（约 691–705 行）；需区分「真短」与「被截断/解析失败」。

### B2. 工程化改进（建议最小集）

- **可观测**：`PromptTrainingRecord.training_metrics` 中写入 `llm_status`、`response_len`、`error_class`（不含密钥）。
- **阈值**：`200` 改为配置项；或对「仅输出 diff 片段」模式单独处理。
- **重试**：对可重试错误（429/5xx）有限次重试。
- **账户绑定**：`account_prompt_bindings` 为空时，确认实盘是否仅依赖 `AIStrategy.master_prompt_template_id`；若双轨，文档写清**以哪条为准**，避免进化后仍读旧绑定。

### B3. 与 `ENABLE_EVOLUTION_FEEDBACK` 的关系

- **现状**：该 flag 主要短路 `trading_decision_interface.adapt_params`，**不控制** `_evolve_prompt`。
- **P4 文档要求**：在 `DEV-README.md` 或系统设置页文案中写清两条链路，避免用户以为「关掉进化反馈 = 关掉 prompt 进化」。

---

## 六、工作流 C — 可观测性与验收脚本

### C1. DB 健康查询（可放进 `scripts/` 或运维手册）

- `atas_factor_cache`：最近 24h 行数；`value` JSON 中 null 键比例。
- `prompt_training_records`：`optimized_prompt_id IS NOT NULL` 计数；最近 `status` 分布。
- `prompt_templates`：`created_by='prompt_evolution'` 计数。
- `ai_strategies`：`master_prompt_template_id` 分布是否仍 100% 为 1。

### C2. 日志关键词

- `[Learning] LLM 返回的优化提示词过短`
- `[Learning] 提示词已进化至 v`
- 融合否决：`fusion` / `verdict`（具体子串在实施时固化）

---

## 七、Feature Flags（建议命名）

| 变量 | 默认 | 含义 |
|------|------|------|
| `RISK_P4_ENABLED` | `false` | 总开关（可选） |
| `RISK_P4_FACTOR_PROMPT_INJECT` | `off` / `shadow` / `on` | 模板是否注入因子段落（shadow = 只打日志不写 snapshot） |
| `RISK_P4_PROMPT_EVOLUTION_STRICT` | `false` | 为 true 时进化失败是否告警升级（钉钉/指标） |

具体命名可在实现时与现有 `RISK_*` 风格对齐。

---

## 八、分阶段上线（建议）

| 阶段 | 内容 | 回滚 |
|------|------|------|
| P4-0 | 仅修 A3（orch_directions）+ A1 null 语义 | 单 PR，低风险 |
| P4-1 | A2 模板注入 shadow（日志验证长度与内容） | 关 flag |
| P4-2 | A2 on + A4 flag 接通 | 关 flag |
| P4-3 | B2 可观测 + 阈值可调 + 一次成功进化 | 保留旧模板，策略回滚 `master_prompt_template_id` |

---

## 九、风险与缓解

| 风险 | 缓解 |
|------|------|
| Prompt 变长 → 成本与延迟上升 | 硬编码摘要长度；缓存同 symbol 的因子段落 |
| 进化后的 prompt 破坏占位符 → 运行时 format 失败 | 进化指令已要求保留占位符；增加「format 校验」单元测试 |
| 融合否决过严 → 机会减少 | P4-3 前用 shadow 统计否决率 |

---

## 十、任务清单（实施时勾选）

- [ ] A1：`composite_v3` 序列化与单测
- [ ] A2：三模板增加因子块 + `ai_decision_service` 对齐
- [ ] A3：`orch_directions` 初始化与异常处理收紧
- [ ] A4：`FACTOR_SIGNAL_ENABLED` 接入或删除
- [ ] B1：抓取 3 次真实失败样本（日志 + metrics）
- [ ] B2：`PromptTrainingRecord` 增强 + 重试 + 可配置长度阈值
- [ ] B3：文档澄清 `ENABLE_EVOLUTION_FEEDBACK` vs prompt 进化
- [ ] C1：验收 SQL / 小脚本入库 `scripts/`
- [ ] 回归：现有 P2/P3 单测全绿 + 新增 P4 单测

---

## 十一、修订记录

| 日期 | 修订 |
|------|------|
| 2026-04-23 | 初稿：基于因子/提示词进化审计结论 |
| 2026-04-23 | 正文迁至仓库根目录 `decisions_p4.md`，便于 IDE 打开 |

---

**说明**：本文为规划草稿；实施前应在 `docs/research/` 或 stage runbook 中增加「P4 实施记录」段落，并沿用 P2/P3 的「证据优先、可回滚」原则。
