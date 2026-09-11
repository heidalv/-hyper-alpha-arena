# 因子挖矿系统升级设计（2026-08-30）

## 背景与根因（全部有代码/日志证据）

近期 6 次全量挖矿（19/68/60/20/26/59 候选）**0 晋升**。根因五项：

| # | 根因 | 证据 |
|---|---|---|
| R1 | 候选生成 = 14 个固定模板参数扫描，mom 是 rev 的镜像，无真实搜索空间 | `_mine_candidates` 源码 |
| R2 | 清洗初筛单币化：挖掘用 9 币面板选，清洗用第一个币（BNB）单币枪毙 | `_purge_and_select`: `df = list(dfs.values())[0]`；实测 20/20 全灭于初筛 |
| R3 | 统计功效不足：4h 验证段仅 360 根（60 天），DSR 跨 27 试验校正后 ICIR 1.31 也不显著 | 日志 `sample_len=360 n_trials=27 dsr_sig=False pbo=0.86` |
| R4 | 时间预算 1800s 截断 GP/MCTS 搜索 | 加强档设 300×20 代/500 迭代，实际每轮只评 20-68 个 |
| R5 | 影子期硬门槛 20 天（paper_min_days×2），即使晋升也长期见不到实盘 | `_auto_oversight_approve` |

数据覆盖（crypto_klines，BTC）：4h≈400 天、1h≈240 天、15m≈90 天、5m≈55 天。
算子库支持 33 个 op + funding/oi/basis 等永续字段（PERP_FACTOR_EXPRS 已有 4 个永续模板）。

## 修改设计

### M1 评估窗口与预算（对应 R3 部分 + R4）
- `_PERIOD_SPLIT_DAYS` 按数据覆盖调整：
  - 4h/8h/1d: (180,60,30) → **(300,60,30)**（train 1800 根，验证 360 根×9 币面板）
  - 1h: (90,30,15) → (150,45,30)；30m: (60,20,15) → (90,30,20)
  - 15m: (30,10,10) → (45,15,10)；5m: (30,10,10) 不变（数据只有 55 天）
  - 2h: (120,45,30) → (200,60,30)
- `.env`: `FACTOR_EVO_BUDGET_MAX_SEC=5400`（代码上限 7200 内；凌晨执行不影响交易）
- 风险控制：门禁零改动；split_insufficient_data 防线保留。

### M2 初筛面板口径（对应 R2，核心手术）
`_purge_and_select` 中 `factor_series_fn`/`return_series` 从单币改为**逐币拼接面板**：
- 对 dfs_val 每个币：算因子序列 → **按币 z-score 标准化**（等权，防单币量纲主导）；
  前向收益同源同币。
- `pd.concat` 拼接为 pooled 序列交给现有 `evaluate_factor`（IC/ICIR/单调性/换手/半衰期
  全链路复用，purge_pipeline 零改动）。
- 新增诊断日志：逐币正 IC 计数（如 `panel_ic_pos=7/9`），可审计。
- 统计功效：T 从 360 → 360×9≈3240，DSR 的 t=ICIR×√T 检验力数量级提升。
- 边界处理：单币因子表达式评估失败 → 跳过该币；全部失败 → 候选拒（原行为）。

### M3 候选多样化（对应 R1）
`_mine_candidates` 新增 6 族 11 个模板（全部用现有 OP_REGISTRY 算子+字段）：
- `vwapdev{20,50}`: (close−mean(vwap,w))/std(close,w) —— 均值回归距离
- `brk{20,50}`: (close−min(low,w))/(max(high,w)−min(low,w)+ε) —— 区间突破强度
- `body{10,20}`: mean((close−open)/(high−low+ε), w) —— K 线实体压力
- `rngpos{10,20}`: mean((close−low)/(high−low+ε), w) —— 收盘在区间位置
- `vshake{10,20}`: corr(volume, abs(returns), w) —— 量能-波动确认
- `ema_gap{12,26}`: (close−ema(close,12))/ema(close,26) —— EMA 动能比
（funding/oi 族已有 PERP_FACTOR_EXPRS，full 模式自动带上；quick 模式保持种子集。）

### M4 影子期统计达标提前毕业（对应 R5）
`_auto_oversight_approve` PAPER→SMALL_LIVE 增加早毕业路径：
- 原路径保留：sharpe≥1.5×1.0 且 days≥20
- 新早毕业：sharpe≥**2.0**×1.0 且 days≥**10**（paper_min_days 原值，不破下限）
- 语义：表现越强、影子期越短，但统计标准更严；不放水。

### M5 不改动清单（明确边界）
- 统计门禁：min_icir=0.40、max_pbo=0.35、DSR required、test_ic≥0.01、IC 衰减隔离——全部不动
- 冷启动豁免、quick 模式禁 GP/MCTS（止血设计）——不动
- 容量门槛 $50k（昨调）——不动

## 执行计划

| 阶段 | 内容 | 验证 |
|---|---|---|
| P1 | M1 窗口表 + .env 预算 | 单测：分档表断言；日志确认 need/got |
| P2 | M2 面板初筛手术 | 合成数据单测：面板构造/标准化/拼接正确性 |
| P3 | M3 新模板 | 单测：新模板表达式可 parse+evaluate 出有限值 |
| P4 | M4 影子早毕业 | 单测：四象限（强/弱 sharpe × 短/长天数）断言 |
| P5 | 回归测试 + 真实 4h quick 一轮（出进程）+ 冒烟 | 流水线日志出现面板口径；无异常；API 健康 |

回滚：全部改动 env 或函数级可回退；.env 不设新变量即回旧行为（M2/M3/M4 为代码级，
保留原逻辑注释，git 可回滚）。
