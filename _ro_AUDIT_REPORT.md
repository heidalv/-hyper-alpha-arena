# 只读审计：评分科学性 (A) + 开仓准确率前后对比 (B)
仅 SELECT；**未改任何受版本控制文件、未重启服务**。注意：`alpha_arena` 有 58 张表 `FORCE RLS`，不设 `SET app.is_admin='on'` 时 `count(*)` 恒为 0（`signal_trade_feedback` 实测 0 vs 实际 622,358）。
脚本：`_ro_b1.py`…`_ro_b10.py`（只读）。

## 结论先行
**(A) 不科学。** 混合打分中心当前对开仓决策**零影响**（shadow 档且 fusion 晋级门永闭）；三通道中「thesis 通道」因路径 bug **恒为空**、通道A 的 LTR 模型**恒不可用**；秩相关实现有系统性 **+偏差**；「逐单增量归因」经数据实证是**覆盖度代理，不是因子 alpha**（1,474 个因子名只产生 238 个不同取值，其中 803 个完全相同）。
**(B) 不足以判定。** 所有指标方向为正，但升级后仅 19–27 笔 / 238 条信号，**无一项显著**（p=0.147–0.243）。不是"有提升"，是"还测不出来"。

## A. 评分科学性

### A1 混合打分中心 `backend/services/hybrid_scoring/`
- **shadow 档，零接管**：`config.py:23` 默认 shadow，`.env` 无 `HYBRID_SCORE_MODE`；消费点 `auto_coin_selector.py:816` 仅 `mode=="fusion"` 才生效 → **当前完全不参与选币**。
- **fusion 门永闭（结构性不可晋级）**：`service.py:306` 要求 `ic_fused≥0.05`，而 `data/hybrid_scoring/ic_stats.json` 实测 `ic_fused:null, days:0` → 取 0.0 → 恒 `<0.05` → fail-closed。
- **thesis 通道 (A1) 恒空 — 错误**：`evidence.py:26` 拼成 `backend/services/hybrid_scoring/data/ai_coin_unified`，真实目录是 `backend/services/data/ai_coin_unified`（`ai_coin_unified.py:32`）→ `_thesis_map()` 永远返回 `{}`。实测 `score_log.jsonl` 30/30 条 `thesis.present=false` 且 `arm_fields` 无 A1 键。
- **A1 的 IC 结构性不可测 — 错误**：`service.py:224` 只收集 `("A0","A2","A3")`，但 `service.py:253` 上报 `ic_thesis_mixed=mean(ics["A1"])` → 恒 null、`per_arm_days.A1` 恒 0（与 ic_stats.json 实测一致）。
- **tier→分数是硬编码先验 — 存疑**：`service.py:84` `{"long":0.9,"mid":0.8,"short":0.6}`，缺失默认 0.6；无校准无样本依据。A1 再按 `fusion.py:128` 固定 50/50 与通道A百分位相加。
- **通道A 实为静态线性加权，非模型 — 存疑**：`ltr.py:174` 门要求 `best_iteration≥20`，`model_meta.json` 实测 `best_iteration=5` → 恒降级 `ic_weight`（score_log 30/30 `backend=ic_weight`）。`ic_weights` 中 7/12 特征为 0，`dd_20+turnover_20+rev_1+close_z_60` 占 **99.2%** → 有效特征仅 4 个。`ltr.py:212` `0.5+v/4.0` 的 `4.0` 无依据（v 为权重和=1 的 z 加权均值，std≲1）。
- **秩相关实现有 +偏差 — 错误**：`ltr.py:40` `cov/(sd*(n-1)/n)` = ρ·n/(n-1)，**系统性放大 |ρ|**：n=30 → +3.4%，n=5 → +25%（可致 |ρ|>1）。`_ndcg10`（`ltr.py:44-57`）的 IDCG 只用"模型自选 top10"的 gain 排序，非全样本 top10 → 实测 `test_ndcg10=0.8115` **不能证明选币质量**。
- **模型指标本身是噪声**：`model_meta.json` `valid_rank_ic=-0.0313`（反号）、`test_rank_ic=+0.037`，均 <0.05。
- **融合权重含占位值 — 存疑**：`fusion.py:22` `_K_SHRINK=4.0`；`fusion.py:79` `std_diff` 缺省 0.25，而 ic_stats 实测正是 0.25 → `lam=min(1,4×0.25)=1` **恒全收缩**，`w_ic` 永不生效，退化为 regime 先验（0.5 / 趋势态 0.6）。
- **量纲/语义混加 — 错误**：`fusion.py:126` 两臂都是百分位（自洽）；但 `service.py:309` 把 composite **绝对分**与 hybrid **百分位**线性混合 → 两个语义不同的 [0,1] 相加。

### A2 因子评估管线 `backend/services/factor_engine/`
- **`factor_evaluation_pipeline` 自身不设 IC 口径，且接线错误 — 错误**：`factor_evaluation_pipeline.py:282` 把 `forward_returns` 传进了 `FactorEvaluator.evaluate_factor` 的 **`close_prices` 形参**（`factor_evaluator.py:87`）→ `factor_evaluator.py:116` 对前瞻收益**再做一次 `pct_change(fwd)`+`shift(-fwd)`**。该方法全仓无调用点（死代码）。
- **IC 不是横截面 rank IC — 错误**：`factor_evaluator.py:283-294` 是**单序列时序滚动 Spearman**，window=`max(20, len//10)`；相邻窗重叠 (w−1)/w≈**95%** → `ic_std` 被低估、`icir=ic_mean/ic_std`（`:147`）被**高估**、`ic_positive_pct`（`:148`）把重叠窗当独立样本。全文件**无 Newey-West/HAC/聚类/有效样本数**。
- **无多重检验校正 — 错误**：`factor_evaluator.py:60` `IC_GRADE_A=0.05` 面对 ~97 因子 × 20 轮反复评估，Bonferroni α≈2.6e-5 需 |t|≈4.2，实测因子 t≈1.5–2.6 → **全部不达标**。仓库有 DSR/PBO 机器（`factor_backtest_scorer.py:1083-1141`）但 IC 路径不调用。
- **评级用 |IC| 且单调性不校验方向 — 存疑**：`factor_evaluator.py:171` `abs_ic` → IC=−0.08 的反向可用因子也判 A；`_compute_monotonicity`（`:327-345`）取 `max(正,负)/总` → 单调递减因子得 1.0，噪声得 0.5。
- **"评估退化"与"真实 IC=0"混同 — 存疑**：`:111`/`:127` 样本不足时直接返回 `ic_mean=0.0, grade=F`，下游不可区分。
- **单例竞态**：`factor_evaluator.py:381` 每次调用改写单例 `forward_period`，多线程下 1h/4h/1d 互相污染。
- **WFO 门被放宽而非收紧 — 存疑**：`.env` `WFO_IC_MAX_P=0.15`（轮47 由 0.05 调档）→ 假阳性率上升。

### A3 「逐单增量归因」SignalFeedback — 数据实证：不是增量
- 真实表 = `alpha_arena.signal_trade_feedback`（622,358 行；`factor:` 前缀 609,249 行）。**`factor_signal_log` 表不存在**（全库 0 命中，唯一提及是设计文档 `docs/因子与LLM统一策略架构_诊断与设计_2026-09-17.md:76` 的占位符）。
- **公式**：`signal_feedback_tracker.py:276` `contribution[f] = mean(trade_pnl_pct | f 活跃) − mean(所有 factor: 行)`。**没有"不活跃"对照、没有反事实基线、无加性分解** —— 文档声称的 active-vs-inactive 在代码里不存在（`:5-6`/`:239` 与代码矛盾）。
- **数据实证（决定性）**：入口快照记录**整条因子向量**（`signal_feedback_tracker.py:92-117`），故每因子的"活跃样本"几乎同一批交易。PRE 期 1,474 个因子名只产生 **213 个不同行数 / 236 个不同均值 / 238 个不同 (行数,均值) 对**，其中 **803 个因子共享完全相同的一对值 `(n=308, mean=−0.00085911)`**（`_ro_b10.py` P3/P4）；`corr(行数, 因子均值)=+0.295`。→ **该数值度量的是"因子出现在哪些交易上"，不是因子 alpha。**
- **复核 SQL**：`SELECT signal_type,count(*) n,avg(trade_pnl_pct) m FROM signal_trade_feedback WHERE trade_pnl_pct IS NOT NULL AND created_at<'2026-09-17' GROUP BY 1;` 再按 `(n, round(m,8))` 分组计数（`_ro_b10.py`）。
- **同源重复计数 — 错误**：平均每笔交易写入 **208.8** 行（min 3 / max 2268）；`funding`/`oi`/`liquidation` 各覆盖全部 2,797 笔 → 同一笔盈亏被归因 200+ 次（`_ro_b9.py` C1/C2）。
- **量纲不一致 — 错误**：同表两套标签两套权重——`trade_pnl`(USD) 用于 `factor_ic_evaluator.py:331`，`trade_pnl_pct`(ROI) 用于 `signal_feedback_tracker.py:262`。贡献值（ROI 差）被当**权重乘子**用（`factor_weighting.py:599`），而 `factor_decay_monitor.py:194` 对**同一个数**用 −0.002 阈值 → **两消费者阈值差 250×**。
- **符号信息被销毁 — 错误**：`signal_feedback_tracker.py:208` `v−min_val+0.01` 后归一化 → **最差信号仍得正权重**；注释称"softmax"但无指数（`:204`）。
- **静默 0 填充 — 错误**：`:262/:268` 过滤 `trade_pnl IS NOT NULL`，却取 `trade_pnl_pct or 0` → `trade_pnl_pct` 为 NULL 的 **38,374 行 (6.2%)** 被当 **PnL=0** 计入均值。
- **时区错配 — 存疑**：`:244` 用 `datetime.now(timezone.utc)` 比 naive `created_at`（实测为 CST）→ 30 天窗偏移 8h。

### A4 组合层与前视
- **无 bp/概率/百分数混加**（这条干净）：分数统一归一到 [0,1] 百分位后加权（`fusion.py:118-126`，`channel_b.py:82` 做了 `/10.0`）。**但存在"绝对分 × 百分位"混加**：`service.py:309`。
- **无经典前视**：特征只用 ≤t（`features.py:6-7`），标签 `shift(-1)`（`features.py:119`）。**但存在锚点错配**：`service.py:163/174-182/192` 的 24h 前瞻收益按 **symbol 缓存**，`pending_syms[sym]=ts` 会被同币后一条覆盖，而 `ret_cache` 按 `symbol` 取用 → 所有待回填条目共用**同一个锚点 ts**，与其自身时刻无关。
- **样本量**：`score_log.jsonl` 仅 30 条、**outcome 回填 0/30**（`ic_stats.json days=0`）→ hybrid 的 IC 永不可算；thesis 通道 0 有效样本。

### A5 证明评分有效性的证据缺口（全缺）
1. **无 IC 时序**（`ic_stats.json` days=0，四臂 IC 全 null）；2. **无校准曲线**（tier→0.9/0.8/0.6 与 `0.5+v/4` 均无分箱校准/可靠性图）；3. **无样本外**（融合权重退化为先验，从未 OOS 验证）；4. **无 PBO/DSR/多重检验校正**；5. **归因无反事实**（无"去掉该因子"对照、无加性分解、无显著性）；6. **thesis 通道 0 样本**，`evaluate_hits` 从未成功回填。
## B. 开仓准确率：升级前 vs 升级后
**口径**：按**开仓/决策时间**分桶；指标 = ①已平仓交易胜率 ②平均单笔 PnL ③平均持仓收益。PRE = `< 2026-09-17 00:00 (CST)`，POST = `>=`。边界依据：`logs/restart-r*.log` 显示 09-17 00:05→17:23 共 24 次重启，09-18 03:17、09:21 各一次。

| 指标（表） | PRE | POST | Δ | 检验 |
|---|---|---|---|---|
| 胜率 `trade_facts.ts` | 40.75% (n=**2471**) | 51.85% (n=**27**) | +11.1pp | z=1.17 **p=0.243**，95%CI[−7.6,+29.7]pp |
| 平均单笔 PnL（同上, USD） | −0.395 | +0.267 | +0.662 | — |
| 胜率 `signal_trade_feedback` 逐单去重 | 41.43% (n=**2778**) | 57.89% (n=**19**) | +16.5pp | z=1.45 **p=0.147**，95%CI[−5.8,+38.7]pp |
| 平均持仓收益（同上 `trade_pnl_pct`） | −0.073% | +0.284% | +0.356pp | t=1.29，95%CI**[−0.19,+0.90]pp 含 0** |
| 信号命中率 `signal_ledger.hit` | 47.82% (n=3233) | 47.90% (n=238) | **+0.08pp** | 无变化 |
| 平均超额 `signal_ledger.excess_bp` | −61.9 bp | −78.4 bp | **−16.5 bp（更差）** | 未判定 |

**结论：不足以判定。** 方向全为正，但**无一项通过显著性检验**；POST 窗口仅 09-17 00:49 → 09-18 05:04（≈1.2 天）。
**需要多少样本**（80% power, α=0.05）：胜率(`trade_facts`) 每组 **313 笔** → 按 23.0 笔/日 ≈ **14 天**；逐单胜率 每组 **141 笔** → 按 20.9 笔/日 ≈ **7 天**；平均持仓收益（POST sd=0.0120）要检出 +0.36pp 需每组 **≈1,740 笔**。
**边界敏感性**：cut 改 09-17 12:00 → POST n=11（胜率 45.5%）；cut 改 09-18 03:17 → POST **n=2**。**结论随边界翻转，不可作验收依据。**
**PRE 本身不平稳**：09-04~09-16 每日仅 2–12 笔、日均值在 −23.7 ~ +17.5 间跳动（`_ro_b7.py` T4）→ 以 PRE 全期均值作基线不可靠。

### B-专：逐单增量归因时间切分 + 最差因子
- 文档"86,432 样本 / avgPnL −0.095%"**可复现但会漂移**：我实测 PRE 期 `factor:` 行 562,045、全局均值 **−0.086%**（同量级）；日志显示同口径在 **79,964 → 133k** 间漂移（`logs/backend.log` 09-18 03:19 / 05:10 / 09:19），因 lookback 窗与写入速率不同步。**"86432"是某时刻快照，不是稳定样本量。**
- **因子级前后切分不可做**：POST 期任一 `signal_type` 最多 **71 行 / 19 笔**；两时代都 ≥30 行的因子有 213 个，但 POST 每因子 ≤19 笔 → **样本不足，不作因子级结论**。
- **最差因子 `evo_3d51976a92a07bea` 当前状态**：`data/factor_decay_status.json` = `current_ic 0.0 / historical_ic 0.0 / trend "dead" / recommendation "retire"` → `factor_decay_monitor.py:243-244` 因 `historical_ic(0.0) < retire_ic(0.01)` 返回惩罚 **0.0** → **权重已归零，桥确实生效**。其归因值 PRE −0.006689(160 行) → POST +0.00239(61 行)，但该数值属上文"cohort 覆盖度"产物，**不是该因子的 alpha**。
- **⚠️ 同一机制的反向风险**：`factor_decay_monitor.py:221-225` 给桥建档因子播种 `historical_ic=0.0` → `:243` 的"双确认"**自动成立**；若全局常数 ≤ −0.004（`threshold*2`），则**全部因子同时 retire → 惩罚 0.0 → 加权层归零**（`factor_evaluation_pipeline.py:221-222`）。

## 未验证 / 数据不足（显式标注）
- 混合打分中心的评分有效性：**未验证**（shadow + IC 门永闭 + 30 条日志 0 回填）。
- thesis 通道 (A1)：**零有效样本**，有效性无法评估。
- 开仓准确率提升：**不足以判定**（p=0.147–0.243；需 7–14 天同等流量）。
- IC 是否与实盘方向一致：**未验证**（IC 路径用 `tanh(raw)`，实盘用 rolling z，符号可反）。
- `decision_snapshots`(alpha_analytics)：仅 54 行有 `pnl_pct` / 58 行 `executed`，**样本不足**，未采用。
- 未做：PBO/DSR、Newey-West、有效样本数、校准曲线、真样本外。
- 清理说明：删除了我子代理生成的 `_ro_inv_conns.json`（内含明文 DSN）；未触碰任何受版本控制文件。
