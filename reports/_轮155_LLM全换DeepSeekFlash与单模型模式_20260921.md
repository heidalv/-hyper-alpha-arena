# 轮155 —— LLM 全部换成 DeepSeek Flash、去掉双模型交叉验证

日期：2026-09-21
用户指令（原话）：
> 现在llm，全部换成deepseek fl 吧 不用minimax了
> 不要用pro
> 那就去掉这个双模型验证，这个也是累赘

---

## 一、先说一个硬约束（实测，不是推断）

`reports/_probe171_dual_call_reality.txt` / `_probe172_single_model_live.txt`：

| 跑法 | 结果 |
|---|---|
| 单票（只 deepseek） | `status=degraded` `consensus_score=0.375` `accepted=False` |
| deepseek + deepseek | 网关自己打印「deepseek 与 deepseek 同为模型 deepseek-v4-flash → **视作单票**」 |
| deepseek + glm_opencode_alt | 两票不同模型，可正常走共识 |

- `CONSENSUS_THRESHOLD=0.7` 衡量的是**两票一致性**；单票被硬压 ≤0.6 ⇒ 永不 `accepted`
  ⇒ 主脑论题永不成立 ⇒ 中线/长线常规开仓链整条停摆。
- DeepSeek 侧 `/models` 只有 `deepseek-flash` 与 `deepseek-v4-pro`
  （`deepseek-chat` / `deepseek-reasoner` 请求都回显 `deepseek-flash`，是别名）。
  用户明确"只用 flash、不用 pro" ⇒ 没有第二个模型能当第二票。
- **所以"去掉双模型验证"不是可选项，而是"只用 flash"的前提条件**（用户已明确要求去掉）。

---

## 二、改了什么

### 1) 单模型模式（`backend/services/analysis/model_gateway.py`）
新增 `single_model_mode()`（`ANALYSIS_SINGLE_MODEL_MODE`，默认 **true**）与
`single_model_min_conf()`（`ANALYSIS_SINGLE_MODEL_MIN_CONF`，默认 `0.0`）。
`dual_call` 在单模型模式下走新分支 `_single_model_call`：

- 只调**一条**传输（首条主票；不可用时按候选池依次顶替 —— 这是可用性问题，保留）；
- 该票的 JSON **就是结论**：`status=ok`、`consensus_score = 模型自报 confidence`；
- `accepted = confidence ≥ 单模型门槛`（默认 0.0，即不额外设卡）；
- "该不该开"由**下游既有闸门**决定：V5Gate 置信门槛 / 风控官 / 位置闸 / learned 带 /
  蒙特卡洛 / 组合预算 / 段闸 + `brain.consensus_is_tradeable` 自身的方向与失效价校验；
- `comparison` 与 `notes` 明确写 `verification="disabled"`、**无第二方验证** ⇒ 审计可见。

**回滚**：`ANALYSIS_SINGLE_MODEL_MODE=false` + `ANALYSIS_PRIMARY_TRANSPORTS=<两条不同模型>`
⇒ 双票盲评/仲裁协议原样还在（`reports/_probe172` 已验：单模型模式下永远只发一票）。

### 2) MiniMax 摘除（代码 + 部署）
| 位置 | 改动 |
|---|---|
| `model_gateway.primary_names()` | 默认 `minimax,glm_opencode` → **`deepseek`** |
| `model_gateway.fallback_names()` | 默认去 minimax → **`deepseek,glm_opencode_alt`** |
| `model_gateway.arbiter_name()` | 默认 → **`deepseek`** |
| `news_intelligence_service._annotate_via_gateway` | `("minimax","glm_opencode","ollama")` → `("deepseek", …)` |
| `events/smart_money.py` | 同上 |
| `analysis/quota_guard.py` 快照顺序 | minimax 移出，deepseek 置首 |
| `analysis/tasks.py` 图审 `images_for` | `["minimax","glm_opencode"]` → `["glm_opencode","glm_opencode_alt"]` |
| `mlto/brain_debate._llm_transport()` | 默认回落 `minimax` → `deepseek` |
| `MiniMaxTransport` 类 | **保留注册**（供回滚），但已无任何选择路径指向它 |

`.env`（部署口径，未纳入 git）：
```
ANALYSIS_PRIMARY_TRANSPORTS=deepseek          # 原 minimax,deepseek
ANALYSIS_ARBITER_TRANSPORT=deepseek           # 原 glm_opencode_alt
ANALYSIS_FALLBACK_TRANSPORTS=deepseek         # 原 glm_opencode_alt,deepseek,minimax
ANALYSIS_5H/WEEKLY/DAILY_CALLS_MAP           # 去掉 minimax 条目
ANALYSIS_SINGLE_MODEL_MODE=true               # 新增
ANALYSIS_SINGLE_MODEL_MIN_CONF=0.0            # 新增
```

### 3) 顺带修好的两个**既存**红测试（非本轮引入）
- `test_dual_local_votes_20260904.py`：替身 `complete()` 缺 `images` 形参 ⇒ 4 个 dual 协议
  用例自该形参加入起**恒红**（交互验证的回滚路径静默失去覆盖）。补参后并把用例显式钉在
  双票路径（`ANALYSIS_SINGLE_MODEL_MODE=false`）；另两处断言按当前部署口径更正
  （`.env ANALYSIS_LOCAL_TRANSPORTS=` 为空 ⇒ 默认名单用例需先摘掉部署覆盖）。
- `test_quota_per_transport_20260904.py`：断言里的 minimax 换成 glm_opencode_alt / deepseek，
  保持"各传输额度独立计数"的本意。

---

## 三、验证

- 单模型模式真跑（`_probe172_single_model_live.txt`）：
  `status=ok  consensus_score=0.55  accepted=True`
  `comparison={"single_model":"deepseek","verification":"disabled","model":"deepseek-v4-flash"}`
  且**只调用一次**（无论传几个主票名）。
- 回滚口径（`ANALYSIS_SINGLE_MODEL_MODE=false`）单票仍 `degraded ≤0.6 accepted=False`（测试钉住）。
- 测试：新增 `test_llm_deepseek_only_20260921.py`（7 项：默认链路=deepseek、部署 .env 无 minimax、
  硬编码顺序表无 minimax、单模型 accepted 语义、门槛开关、回滚口径）。
  相关扫 `-k "llm or gateway or quota or debate or dual or news or env_registry"`：
  **315 passed / 1 failed**（唯一失败 `test_fusion_probe_bypass::test_env_contract_probe_quota_below_live_daily_cap`
  与 LLM 无关 —— 探针日额度 60 ≥ 实盘单币日容量 12 的配置契约，经 stash 复核在 HEAD 上同样失败）。

## 四、代价（如实记录）

1. **没有第二方验证**：模型说 long 就是 long，不再有"另一家模型是否同意"这一层。
   这是用户明确要求的取舍（"这个也是累赘"）。
2. **图审任务失去带图票**：DeepSeek 传输是纯文本（`complete()` 忽略 `images`），
   故 K 线图在本任务里没有票会看 —— 图审实际退化为"深度 K 线数值分析"
   （提示词本身已要求"没有图片就仅依据数值数据作答，不要臆测图内容"）。
3. `consensus_score` 的含义从"两票一致性"变成"模型自报置信度"，历史值不可直接比较。

## 五、取证

- `reports/_probe168_llm_transports.txt`（改动前的传输/DB 配置全貌）
- `reports/_probe169_deepseek_models.txt`（DeepSeek 可用模型实测 + `/models`）
- `reports/_probe171_dual_call_reality.txt`（单票 / 同模型两票 / 异模型两票对照）
- `reports/_probe172_single_model_live.txt`（单模型模式真跑结果）
