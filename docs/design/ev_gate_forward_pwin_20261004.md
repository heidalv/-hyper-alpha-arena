# ⑤ 中长线 EV 闸门改造设计（数据驱动 p_win，2026-10-04）

> 状态：**设计待确认**（尚未实施）。工作流 ① 已产出证据；本文件是 ⑤ 的实施蓝图。

## 1. 现行实现（file:line 证据）

| 位置 | 事实 |
|---|---|
| `backend/services/midlong_ev_gate.py:6-7` | `EV_pct = p_win × (tp_pct × tp实现率) − (1 − p_win) × (sl_pct × sl实现率)` —— **已经是期望值口径**，不是胜率口径 |
| `midlong_ev_gate.py:10` | `p_win` 来自 S1-1 置信度校准器（swing/trend 各一套，**冷启动回退线性映射**） |
| `midlong_ev_gate.py:15` | 开关 `MIDLONG_EV_GATE_ENABLED`，关闭即**影子模式（记录不拦截）**，可秒回滚 |
| `midlong_ev_gate.py:99` | 构造已支持 `p_win_override` |
| `midlong_ev_gate.py:138` | 「p_win：优先外部传入，否则问对应校准器」← **这就是改造的注入点** |

⇒ **闸门数学无需改动**：只要把 `p_win` 的来源从"校准器冷启动线性映射"换成"前向收益实测分桶（含贝叶斯收缩）"，即可完成 ⑤ 的核心目标。

## 2. 实证依据（工作流 ① 的实测数据，12 天 / 7 天前向 / n=335）

| 置信分桶 | n | 实测胜率 | 收缩胜率 | 期望值 |
|---|---|---|---|---|
| 40–50 | 49 | **77.5%** | 64.3% | **+0.0030** |
| 50–60 | 285 | **23.9%** | 24.4% | **−0.0387** |
| 60–70 | 1 | 100% | 35.2% | −0.0091 |

两条结论：
1. **置信度与胜率在样本内反相关**（40–50 桶胜率是 50–60 桶的 3.2 倍），与 `cold_linear` 的单调假设**方向相反**；
2. **只有 40–50 桶期望为正** ⇒ 现行"置信越高越该开仓"的门槛方向可能是**反的**。

**证据强度：初步。** n=49 的小桶、单一 7 天窗口、**单一车道**（`swing_independent/mid` 占 334/335）、下行市况；已用贝叶斯收缩抑制小数噪声，但**不足以直接改门槛**。

## 3. 结构性约束（必须先解决，否则 ⑤ 无意义）

实测 `action/direction` 分布：`master` 车道 **9,555 条决策全是 `direction='hold'`**（无方向观点），12 天**可标注样本 = 0**。

⇒ 对 master 车道，任何 `p_win` 校准都**结构性无数据**。因此 ⑤ 必须分两步：

- **⑤-a（前置）**：让 master 车道的 `hold` **携带方向倾向与强度**（如 `lean=long, lean_strength=30`），**不改变是否开仓**，只产出可校准/可评估信号；
- **⑤-b（本设计）**：用实测分桶替换 `p_win` 来源，并**允许曲线非单调**。

## 4. 设计

### 4.1 新增：`forward_label.p_win_for()`

```python
p_win_for(lane, tier, confidence, *, horizon_days=7) -> {"p_win": float, "source": str, "n": int}
```
- 数据源：`forward_label.label_decisions(...)` 的分桶表（内存缓存 + 定时刷新，默认 30 分钟）
- **收缩**：`p_win = (wins + global_wr × n0) / (n + n0)`，`n0 = FWD_SHRINK_PRIOR_N`（默认 20）
- **最小样本守卫**：`n < FWD_MIN_BUCKET_N`（默认 100）时返回**收缩值**并标注 `source="forward_shrunk"`
- **回退链**：forward → 校准器 → `cold_linear`（任一步失败即回退，绝不因数据问题改变交易行为）

### 4.2 改造：`midlong_ev_gate.py` 注入

- 新增开关 `MIDLONG_P_WIN_SOURCE`：`calibrator`（**默认，行为与今日完全一致**）| `forward`
- `forward` 模式下在 `midlong_ev_gate.py:138` 处改走 `p_win_for()`；失败自动回落校准器
- **影子对比（推荐先跑）**：`MIDLONG_P_WIN_SHADOW=true` 时**两个值都算**、都写进决策快照（`evaluate_verdict_json.p_win_forward / p_win_calibrator`），**闸门仍用校准器** ⇒ 零行为风险地积累对比证据

### 4.3 回滚开关一览

| 开关 | 默认 | 作用 |
|---|---|---|
| `MIDLONG_P_WIN_SOURCE` | `calibrator` | 切回即恢复今日行为 |
| `MIDLONG_P_WIN_SHADOW` | `false` | 只记录不生效 |
| `MIDLONG_EV_GATE_ENABLED` | `true`（既有） | 关闭 → 闸门整体转影子（既有能力） |
| `FWD_SHRINK_PRIOR_N` | 20 | 收缩强度 |
| `FWD_MIN_BUCKET_N` | 100 | 分桶最小样本守卫 |
| `FWD_LABEL_ENABLED` | `false` | 标注总开关（关闭即不产生新数据） |

## 5. 验证清单（实施后必须全过）

1. **ratchet 测试**：默认开关下闸门行为**逐字节不变**（同一输入 → 同一裁决）；`forward` 模式下注入生效；回退链在数据缺失时确实回落
2. **真实数据对比表**：逐分桶打印 `p_win_calibrator` vs `p_win_forward`（含 n 与 source）
3. **一致性抽查**：把 223 条真实成交的胜负与 forward 标注的同方向结论抽样比对（防标注方向错）
4. **不变量**：`master` 车道因无可标注样本，`forward` 模式下必须**全部回落**校准器并记录 `source=fallback_no_data`（不许凭空造数）
5. **影子期达标线**：累计 ≥ 每桶 100 条且 ≥ 300 条总体成交前，**不切** `MIDLONG_P_WIN_SOURCE=forward`

## 6. 风险与诚实边界

- **样本期偏差**：现有 335 条集中在单一 7 天窗口与单一下行市况 ⇒ 若直接启用，可能把"下跌市里的反相关"当成普遍规律（**故默认不启用、先跑影子**）
- **单车道覆盖**：`forward` 只对 `swing_independent/mid` 有数据；其他车道必须显式回落（清单第 4 条）
- **前向收益≠成交收益**：未计手续费/滑点/资金费；实施时用 `tp/sl 实现率` 与费率的既有口径做修正，或明确标注为"理论 EV"
- **不做**：不修改闸门公式、不改任何门槛常数、不动 master 车道的开仓判据（那是 ⑤-a 的独立决策点）
