# 五工作流交接清单（2026-10-04）

> 本文是 ①–⑤ 的**唯一入口文档**：开关、默认值、回滚方式、达标线、改动↔证据对照。
> 所有"默认值"列写的是**当前实际生效值**；标 ⚠️ 的是**需要人工决策才可切换**的项。

## 一、开关总表

| 开关 | 当前值 | 作用 | 回滚方式 |
|---|---|---|---|
| `ANALYSIS_QUOTA_ENABLED` | `false` | LLM 配额总闸（false=随便调用，仅记账） | 改 `true` 恢复限流 |
| `MODEL_GATEWAY_JSON_RETRY` | `1` | 解析/schema 失败时的严格 JSON 纠错重试（修 `mlto_debate` 21% 失败） | 改 `0` |
| `MIDLONG_P_WIN_SOURCE` | `calibrator` | p_win 来源；`forward` 才用数据驱动曲线 | 保持 `calibrator` = 今日行为 |
| `MIDLONG_P_WIN_SHADOW` | `false` | 两个 p_win 都算、**只记录不生效** | 关 |
| `FWD_MIN_BUCKET_N` / `FWD_SHRINK_PRIOR_N` | `100` / `20` | 分桶最小样本守卫 / 贝叶斯收缩强度 | 调大更保守 |
| `FWD_LABEL_TABLE` | `shadow` | 独立标注表：`off`/`shadow`/`on` | 改 `off` 即完全不写 |
| `VOLTARGET_SHADOW` | `true` | 开仓时计算并记录目标波动率影子（**不影响下单**） | 改 `false` |
| `VOLTARGET_ENABLED` | `false` | 未来真正生效用（**当前无消费者**） | — |
| `VOLTARGET_TARGET_ANNUAL` / `_SIGMA_FLOOR` / `_SIGMA_CAP` | `0.35` / `0.05` / `2.0` | 年化目标波动 / σ̂ 截断（防小市值噪声） | 调整 |
| `LEV_SL_RISK_BUDGET` | `0.5` | `sl×lev` 占保证金比例阈值（超则标 warn） | 调整 |
| `RL_GOVERNOR_APPROVED` / `RL_MIN_LIVE_SAMPLES` | `false` / `200` | RL 实盘接管两道门（默认与旧行为等价） | 保持 false |
| `RL_SHADOW_ONLY` / `RL_DECISION_ENABLED` | `true` / `true` | RL 影子（绝不下单） | — |
| `HERMES_L2_ENABLED` / `_L3_ENABLED` / `_L2_AB_ENABLED` | `true` | Hermes 学习内核各级 | 改 `false` |
| `HERMES_AB_MIN_SAMPLES` / `_MAX_DAYS` | `5` / `7` | A/B 样本不足不结案（零样本结案曾使"版本只增不验"） | 改 `0` 恢复旧行为 |
| `HERMES_L3_AUTO_ACCEPT_PAPER` / `_BATCH` | `true` / `20` | L3 提案自动裁决（批量 20） | 改 `false` |
| `HERMES_L4_INCUBATION_ENABLED` | `true` | L4 孵化通道（已绑定 12 策略、会话运行中） | 改 `false` |
| `PAPER_RESET_KEEP_HISTORY` | `true` | 重置只清状态行、**保留已平仓历史**（否则归因永久断链） | 改 `false` 恢复全删 |
| `DC_WATCHDOG_USE_VBS` | 未设 | 看门狗 vbs 旧启动路径（**不落地**，仅回滚用） | 设 `true` |

## 二、指标达标线（未达标前**不得切换**）

| 决策 | 达标线 | 当前 |
|---|---|---|
| 切 `MIDLONG_P_WIN_SOURCE=forward` | 每置信桶 ≥100 条 **且** 总体 ≥300 条独立标注 | 桶最高 30 条（`decision_labels` 刚建立） |
| 放行 RL 实盘接管 | `RL_GOVERNOR_APPROVED=true` **且** `live` 回放样本 ≥200 | live=34 |
| 5x 波动率真正生效（`VOLTARGET_ENABLED`） | 影子 ≥200 笔真实样本，且波动率管理臂**最大回撤显著更低** | 影子 0 笔 |
| 调整杠杆/止损 | 实测当前 `lev=10` + `sl=8%` ⇒ **占用 80% 保证金**（超 50% 预算）。建议：长线降至 2–3x | ⚠️ **待你决策** |

## 三、改动 ↔ 证据对照

| 改动 | 关键证据（实测） |
|---|---|
| `deepseek-flash` 判定修复 | `daily_brief` 由 550ms/0 token 失败 → **11.4s / ok=True / in=12,232 / out=2,799**；`mlto_debate` 失败率 **21.0% → 11.9%**；`midlong_thesis` 0 失败 |
| 配额关闭 | 错误从 `quota degrade: deepseek 5 小时窗已用 64/30` → 不再出现 |
| 决策原因结构化 | 首次可见拦单原因：`[StrictData] tier=long missing=price` |
| 前向标注（①） | n=335：40–50 桶胜率 **77.5%**（期望 +0.0030）vs 50–60 桶 **23.9%**（−0.0387）⇒ **置信度与胜率反相关** |
| 目标波动率（②） | `Spearman(σ̂, 名义敞口)=+0.421`（应为负）⇒ **目标波动率从未生效**；仓位恒 $51 |
| 数据中台（④） | 看门狗**杀健康进程**（04:33:03 日志）→ 修复后 `health timeout but data flowing - NOT restarting`；**E2E：杀进程后 100 秒自动恢复** |
| 编码规范 | 无 BOM 的中文 .ps1 导致 `healthy:null` 假警报，加 BOM 后 JSON 正确 |

## 四、结构性约束（不是 bug，是设计边界）

1. **`master` 车道从不表达方向**：12 天 9,555 条 `direction='hold'` ⇒ 前向标注 0 样本。⑤-a 已让其携带 `lean`（`observability_only`），样本从此可积累。
2. **快照保留 ~8 天 < 7 天标注窗口** ⇒ 曾使标注样本 335→30；已建独立表 `decision_labels`（只增不删）摆脱该约束。
3. **时间口径不一致**：`decision_snapshots.timestamp` 是**本地 naive(UTC+8)**，`crypto_klines.timestamp` 是 **UTC epoch** ⇒ 跨表对齐必须显式转换（`AT TIME ZONE 'Asia/Shanghai'`）。
4. **`VOL_TARGET_ANNUAL` 是空转参数**（仅在 `context_pack.py:34` 白名单，无消费者）。

## 五、运维铁律（本会话踩坑后固化）

1. `start-dev.ps1` **非幂等**：重启必须 `stop-dev` → `start-dev`；不要连点。
2. 不要用 `-NoFrontend` 做常规重启（不会把前端带回来）。
3. **含中文的 .ps1 必须带 UTF-8 BOM**；用 edit 工具改过后要**重新补 BOM**（已有 ratchet 断言）。
4. 生成含 `$`/引号的代码一律用**独立 .py 文件**执行（PowerShell here-string 会吞转义）。
5. 共享库的删除/更新**先 select 出主键清单再按 id 操作**，禁止 `where 非唯一键 in (...)`（本会话三次教训）。
6. 报表层**样本不足不得产出比率**（已护栏）。

## 六、待人工决策

| # | 事项 | 说明 |
|---|---|---|
| 1 | **交易会话是否恢复运行** | 决定 ①②③ 的样本能否积累（当前影子 RL 1 条 / 波动率 0 条） |
| 2 | **杠杆与止损是否调整** | 实测 10x + 8% SL = 80% 保证金，超预算 |
| 3 | 何时切 `MIDLONG_P_WIN_SOURCE=forward` | 需先达达标线 |
| 4 | 证据页是否加入侧边栏 | 现需直接访问 `/learning-evidence` |
