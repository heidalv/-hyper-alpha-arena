# Alpha研究部 · 五Agent闭环升级可行性验证报告

日期：2026-09-17 ｜ 性质：实证验证（非纸面评审）
对象：`docs/Alpha研究部五Agent闭环升级研究_2026-09-17.md` 中声称的"现有资产可复用"假设
方法：只读探针脚本实测（`_probe_feasibility_5agent.py` / `_probe_feasibility_5agent_b.py` / 内联修正轮），
不写任何库、不注册因子、不发真实 LLM 请求。所有结论附证据。

---

## 0. 结论

**可行（GO）——五 agent 闭环的全部核心地基均实测可用，无需新建任何核心基础设施。**
升级本质是"组装既有件"：DSL 编译/审计/沙箱、状态机、经验记忆、RAG、LLM 网关、62M 行行情库、GPU——十项假设十项过验。
放行附带 **6 条落地纪律**（§3，均为"怎么做"而非"能不能做"），其中 2 条（双 alpha_market 库、RLS）不知道就会踩坑。

---

## 1. 逐项验证证据

### V1 expr DSL（③因子工程的编译校验）——✅ PASS

| 检查 | 结果 |
|---|---|
| 算子注册表规模 | **31 个算子**（abs/add/corr/cov/cs_rank/decay_linear/delta/div/ema/greater/less/log/max/mean…） |
| 编译+合成数据求值 | `ts_rank(delta(close,5),10)` 求值成功，300 根 K线 **97% 有限值**（头部窗口 NaN 符合契约） |
| expr_id 确定性 | 同 AST 两次哈希一致，16 位 SHA256 前缀（去重/缓存键可用） |
| 审计拒绝 look-ahead | 负窗口 `delta(close,-3)` → **audit 拒绝** ✓ |
| 解析拒绝未知算子 | `no_such_op` → **ExprError** ✓ |
| 前视算子禁用清单 | `LOOKAHEAD_BANNED_OPS = {rank, cs_rank, scale}`（单序列前视，矿机算子池同步剔除） |

**附加证据**：导入期观察到 `FactorLoader 前视因子跳过加载: ai_gen_volume_break.py (literal_shift_negative)`——
防前视纪律在**运行时真实拦截**，不是纸面规定。

### V2 formula_ops 受限 eval（③沙箱兜底）——✅ PASS

- 15 个 ts_* 算子，`eval(formula, {"__builtins__": {}}, ns)` 求值正常；
- 沙箱逃逸测试：`__import__('os')` → **被阻断** ✓。
- 注：`llm_proposal._trial_eval` 已实现"合成 OHLCV 假数据试算"（≥60 个有限值才放行）——研究文档中
  "最小单测（新增）"应修正为"**AST 路径补等价单测**"（公式路径已有）。

### V3 因子目录（factors_lab 隔离库基础）——✅ PASS

`data/discovered_factors.json`：**730 条**，租户 326。

| 维度 | 分布 |
|---|---|
| source | registry 556 / alpha101_lib 58 / cold_pool 69 / alpha101_lib_scalp 44 / gpu_mine 3 |
| status | candidate 543 / rejected 175 / **active 12** |
| category | registry 625 / **alpha101 102**（WorldQuant 101 校准集已灌入）/ gpu_mine 3 |

**校准集现状修正**：研究文档称"WorldQuant 101 已有"——实测为 **102 条 alpha101 类因子已在库**（含 scalp 变体）。
缺的只是"DSL 重实现 vs 参考实现"的回归门禁，因子本身已在。

### V4 v7 经验记忆（⑤反馈 agent 的记忆基底）——✅ PASS，且**正在活跃写入**

| 指标 | 值 |
|---|---|
| 活跃教训 | **785 条**（pipeline_issue 314 / success_recipe 173 / trajectory 138 / gate_lesson 147 / decay_case 14） |
| 生成报告 | 383 份 |
| 周期覆盖 | L 709 / M 55 / S 22 |
| **最新写入** | **2026-09-17 06:29 UTC（验证当天）** |

结论：进化循环**今天仍在跑**，v7 记忆是热库。⑤ 要做的只是新增"假设层经验"表与归因写入，基底现成。

### V5 lifecycle 状态机（晋级引擎）——✅ PASS

合成指标驱动实测：`DRAFT(审计过)→CANDIDATE→ORTHO→PAPER` 依阈值自动推进；
`has_bug=True→REJECTED` 紧急拒绝生效；`APPROVAL_REQUIRED={SMALL_LIVE, ACTIVE}` 审批闸 +
24h 超时默认拒。**REV-P10/REV-P12 评审关卡有现成挂点**（`needs_approval`），无需新造状态机。
（注：首轮探针传参错误导致假 FAIL——`evaluate_transition(metrics)` 的 metrics 自带 state 字段，修正后全绿。
两次"失败"均为探针缺陷，非产品缺陷，这也验证了接口自洽性。）

### V6 RAG 向量库（①②知识检索）——✅ PASS

`backend/data/rag_chromadb/`：**5 个 collection**（static_knowledge / strategy_lessons / trade_decisions /
trade_memory / trading_wisdom），**2203 条 embedding**（bge-large-zh-v1.5）。
新增 `factor_knowledge` collection 即文献知识卡的落点，零改造。

### V7 LLM 网关（②③⑤调用链）——✅ PASS（带治理条件）

- **治理规则生效**：无租户身份的调用被拒——"拒绝公用默认配置：请为账户配置自有 LLM" ✓（这是好事）；
- **租户级配置存在且可用**：复刻 `alpha_miner.CodegenCritic._load_configs()` 现行路径实测返回
  **2 套 DeepSeek 配置（deepseek-chat + deepseek-v4-flash @ api.deepseek.com），api_key 均已配置**；
- 网关自带并发信号量（`_llm_semaphore` + 低优先级 caller 治理）——五 agent 并发调用直接纳管；
- `llm_quota_usage` 表已有 20,945 条使用记录——用量计量现成，闭环成本可核算。

### V8 行情数据（④回测燃料）——✅✅ PASS，远超预期

**关键澄清：存在两个 alpha_market**。`data/alpha_market.db`（SQLite）只是 2.4 万行的薄本地缓存（数据停在
2026-07-10，1h 仅 8 天）；**真身是独立 Postgres 库 `alpha_market`**（`MARKET_DATABASE_URL`，
`real_factor_backtest.load_real_klines` 即读它）：

| 表 | 规模 | 覆盖 |
|---|---|---|
| crypto_klines | **~6200 万行 / 46 GB** | **2017-08-17 → 2026-09-17（当天）**，14 个周期（1m/3m/5m/15m/30m/1h/2h/4h/8h/12h/1d/3d/1w/1M），**200+ 币种，5 交易所** |
| perp_funding | ~1660 万行 / 4.7 GB | 资金费率（carry 类因子燃料） |
| market_orderbook_snapshots | ~230 万行 / 1.35 GB | 盘口快照（微观结构类因子燃料） |

9 年历史 + 当天新鲜度：CPCV/DSR/衰减曲线等长窗口评估的数据条件完全满足。

### V9 GPU 算力——✅ PASS

`venv-gpu`：**RTX 2080 Ti，CUDA 可用**。`gp_gpu_eval` 批量求值栈可直接承载 gpfactor 工具。

### V10 挖掘产物同构性（③④衔接）——✅ PASS

`backend/data/gp_mine_progress_4h.json` 中的候选即 expr DSL 同款 AST
（`{"op":"ts_argmax","args":[{"op":"min","args":[…]},…]}` + fitness）——
GP 矿机产物与因子工程 agent 的 DSL 输出**天然同构**，③④之间无格式转换成本。

---

## 2. 成本核算（RD-Agent 论文口径对照）

| 项 | 估算 | 依据 |
|---|---|---|
| LLM token | 每轮 <$0.5，月度 <$15（日轮次） | RD-Agent(Q) 全流程实测 <$10（o3-mini）；本链用 DeepSeek（更便宜），每轮 ≈5 假设 × 20-30 次调用 × 2-4k tokens |
| 回测机时 | 现有 GPU 栈消化；62M 行表查询走 (symbol,period,exchange,timestamp) 索引 | V8/V9 |
| 存储 | 知识卡 + 假设经验 ≈ MB/月 级增量 | V4/V6 现库规模外推 |

---

## 3. 放行条件（6 条落地纪律）

1. **双 alpha_market 陷阱**（最重要）：新代码（factors_lab / gpbacktest 工具）取数必须走
   `MARKET_DATABASE_URL`（Postgres），**禁止**读 `data/alpha_market.db`（SQLite 薄缓存，会静默拿到过期短历史）。
   建议在工具层封装单一 `load_klines()` 出口。
2. **RLS 纪律**：所有 LLM 调用走 `alpha_miner` 式 `set_request_identity` + `get_llm_config_for_usage`
   路径——裸连接 `llm_configurations` 查到 0 行（行级安全过滤），绕行会拿到"无配置"假象。
3. **大表查询纪律**：6200 万行表上全表 `COUNT(*)`/无索引聚合会超时（验证中实测踩坑）——
   闭环探针与月度体检脚本一律用 `pg_class.reltuples` 估计值或索引查询。
4. **LLM caller 标识**：五 agent 各自带 caller 标识接入网关信号量治理（低优先级 marker 机制现成），
   避免挖掘循环挤占交易决策的 LLM 配额。
5. **类别别名注册**：导入期警告 `未知因子类别 'mcts_…' 回退 PATTERN`——factors_lab 新增
   source=agent_lab 时同步注册 `_CATEGORY_ALIASES`，避免日志噪音与分类漂移。
6. **命名冲突**（重申研究文档结论）：晋级关卡用 **REV-P10 / REV-P12**（P10/P12 已被 2026-09-10 补丁占用），
   挂点用 lifecycle 现有 `APPROVAL_REQUIRED`，不新造第二套状态机。

---

## 4. 对研究文档的修正清单

| 研究文档原文 | 验证后修正 |
|---|---|
| "WorldQuant 101 已有（alpha101_factors 灌库）" | 实测 102 条 alpha101 类因子在库（含 scalp 变体）；缺的是 DSL 回归门禁，不是因子 |
| "最小单测（新增）" | 公式路径已有 `_trial_eval` 假数据试算；需新增的是 **AST 路径的等价单测** |
| "RAG 需扩 factor_knowledge collection" | 确认零改造可行（现 5 collection / 2203 向量） |
| "gpfactor/gpbacktest 需包装" | 确认 GP 产物与 DSL AST 同构，包装层无格式转换成本 |

---

## 5. 验证资产

- 探针脚本：`_probe_feasibility_5agent.py`（P1-P8）、`_probe_feasibility_5agent_b.py`（P3b/P5b/P7b/P8b 修正轮）、
  内联修正轮（P5c 状态机 / P8c 覆盖度 / Postgres 轻量统计）——按仓库 `_probe/_audit` 惯例保留，可复跑。
- 探针副作用说明：两轮探针各出现一次 FAIL，均为探针自身传参/解析缺陷（P5 签名、P8b 列名、P3 结构），
  修正后全部通过——不构成产品缺陷证据。

**最终判定：五 agent 闭环升级可行性验证通过，可进入 AR-1 里程碑实施。**
