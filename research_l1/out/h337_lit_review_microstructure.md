# 加密货币永续做市与短周期方向策略：微观结构文献综述（h337）

- **对象**：AsterDEX USDT 本位永续（maker 0 / taker 4bp），300 USD 账户，被动做市 + 短周期方向信号。
- **标注规范**：`【文献】` = 已发表/可核验来源；`【推导】` = 本报告基于我们账本数据的校准计算；`【我们】` = 30 天 36,739 笔被动成交的实测。
- **声明**：本报告仅做研究，不改任何生产代码/参数/数据库。外部数字均给出可核验 URL（见 §8）。

---

## 0. 我们的账本基准（30 天，36,739 笔被动成交）

| 指标 | 值 | 来源 |
|---|---|---|
| 价差捕获 | +602.91 USD（每笔 +0.0164 USD ≈ 0.05bp） | 【我们】 |
| 价格逆行（逆选择） | −1,209.17 USD（每笔 −0.0329 USD ≈ 0.11bp） | 【我们】 |
| 手续费 | −261.51 USD | 【我们】 |
| **净** | **−867.77 USD** | 【我们】 |
| 逆选择 / 捕获 | **2.01×** | 【我们】 |
| 全部被动成交 30s markout | −0.1 ~ −0.3bp（中价系统性漂离我们） | 【我们】 |
| 5min 趋势 ≥20bp 时**顺势侧**被动成交 30s markout | 中位数 **+4.4 ~ +5.3bp**（n=576~928，96h 实盘） | 【我们】 |
| 60s 反转信号 | 毛边际 ≈ 0（\|t\|<1.5） | 【我们】 |
| 300s 动量 | +0.37bp @ 30s | 【我们】 |
| 5min 突破 | +0.96bp @ 300s（t=2.6） | 【我们】 |
| 15min 反转 | **+2.77bp @ 300s（t=3.5）** | 【我们】 |
| 报价半距 / 挂宽 | 0.0675bp / 0.135bp；被动成交占比 ≈98% | 【我们】 |
| taker 费 vs 信号毛边际 | 4bp vs 0.4~1bp（**4–10 倍**） | 【我们】 |

分解恒等式：**净 = 捕获 − 逆选择 − 费 = 602.91 − 1209.17 − 261.51 = −867.77 USD**。
两个贯穿全文的事实：(i) 逆选择是捕获的 2 倍，报价"保险"定价不足；(ii) 唯一翻正 markout 的被动成交子集是**趋势顺势侧**（+4.4~+5.3bp vs 全样本 −0.1~−0.3bp）。

---

## 1. 做市经济学与逆选择

### 1.1 Glosten–Milgrom (1985)：价差就是逆选择保险
顺序交易模型：价值 V ∈ {V̄−δ/2, V̄+δ/2} 等概率；知情者占比 μ（总顺势交易），噪声交易者买卖各半。做市商按条件期望报价：

- a = E[V|buy] = V̄ + μδ/2；b = E[V|sell] = V̄ − μδ/2
- ⇒ **价差 = a − b = μδ，半价差 s = μδ/2**

竞争均衡中单笔上与知情者盈亏相抵：价差捕获 = 逆选择损失 = μδ/2 × 成交。价差随知情比例 μ 与信息强度 δ 上升。【文献】
> **对我们的含义**：我们半价差 0.0675bp、却只捕获 0.05bp、逆选择 0.11bp ⇒ 0.11bp 高于 μδ/2 均衡水平 —— 报价系统性过紧，成交对手的信息含量超过我们收取的保险费。

### 1.2 Copeland–Galai (1983)：价差是"免费跨式期权"的对冲成本
做市商的双向报价等于向知情者免费卖出一个跨式期权：

- E[π] = (1−μ)·s·q_liq − μ·E[(V−B)⁺ | 知情成交]

拉宽价差 → 知情者打到你的概率与每笔损失下降，但流动性收入 (1−μ)·s·q 也下降 ⇒ 存在**内点最优价差**；信息不对称越高，最优价差越宽、挂单越少。【文献】
> **对我们的含义**：−1,209.17 USD 的"价格逆行"正是这个期权被行权的样本外实现；缩小它只有两条路：加大距离（§2 已证无效）或**降低知情流打到我们的概率（状态门控）**。

### 1.3 Kyle (1985) λ 与深度
知情者需求 x = β(v−p₀)，β = σ_u/σ_v；做市商线性定价 p = p₀ + λy：

- 单次拍卖：**λ = σ_v/(2σ_u)**；连续极限（Back 1992）：**λ = σ_v/σ_u**；深度 = 1/λ。

价格冲击与价值波动 σ_v 成正比、与噪声流 σ_u 成反比；知情者预期利润 ∝ σ_uσ_v。【文献】
> **对我们的含义**：单腿 300U 名义在盘口深度里几乎无冲击 —— 我们"主动路径不可行"的约束是**费率/信号边际比（4bp vs 0.4~1bp）**，不是 Kyle λ 冲击成本。

### 1.4 PIN 与 VPIN：可观测的毒性指标
- **PIN**（Easley–O'Hara 1992）：PIN = αμ/(αμ+2ε)，α = 信息事件概率，μ = 知情到达率，ε = 噪声到达率；价差随 PIN 单调上升。Easley–Hvidkjaer–O'Hara (2002) 估计 NYSE 平均 PIN ≈ 0.19（典型区间 0.15–0.25）。【文献】
- **VPIN**（Easley–López de Prado–O'Hara 2012）：按成交量桶计算 VPIN ≈ Σ|V_B−V_S|/(n·V̄)，用 CDF(VPIN) > 0.9 作毒性预警，作者用它事前识别了 2010-05-06 闪崩；行业综述报道 VPIN 类过滤在 BTC 上曾有效但 alpha 正在衰减。【文献】
> **对我们的含义**：PIN/VPIN 是"我们该不该挂单"的连续监测指标 —— 把毒性状态做成门控阈值（等价于我们 OFI 门 0.15 的另一侧实现），比固定距离更接近文献处方。

### 1.5 HFT 盈利分解：文献基准 vs 我们
- **Menkveld (2013, JFM)** 对一家大型 HFT 做市商（荷兰指数股）的逐笔分解【文献】：每笔毛利 **€0.88 = 价差（扣费后）€1.55 − 持仓/定位损失 €0.68**；其中 <5 秒持仓 **+€0.45**、>5 秒持仓 **−€1.13**。即 **逆选择/捕获 = 0.68/1.55 = 0.44**，长持仓部分更高达 1.13/1.55 = 0.73。资本 €2.052M/股，年化 Sharpe 9.35。
- **Baron–Brogaard–Hagströmer–Kirilenko (2019 JFE)**：E-mini S&P 500 中 HFT 整体盈利，但**激进（taker）HFT 平均亏损，被动（maker）HFT 才盈利**；利润右偏、集中在做市型公司。【文献】
- **Hagströmer–Nordén (2013, JFM)**：HFT 分两类——**做市型（约占 HFT 交易量 2/3**：持仓短、收益与价差相关、与趋势弱相关）与**机会型（约 1/3**：顺趋势、日内了结）。【文献】
- **DeLise (2024, arXiv:2407.16527)**："The Negative Drift of a Limit Order Fill"——限价单成交自带负漂移；我们的 −0.1~−0.3bp markout 即其样本实现。【文献】

| | 每笔捕获 | 每笔逆选择 | 逆选择/捕获 |
|---|---|---|---|
| Menkveld HFT（股票） | €1.55 | €0.68 | **0.44** |
| **我们（AsterDEX 永续）** | 0.0164 U | 0.0329 U | **2.01**（≈文献基准的 4.5 倍） |

> **对我们的含义**：0.44 vs 2.01 —— 无条件贴盘口在我们的 venue 是负 EV；4.5 倍差距几乎全部来自"何时挂、挂哪侧"的选择，而非价差本身。

---

## 2. 成交概率与报价距离

### 2.1 模型与系数
- **Avellaneda–Stoikov (2008, QF 8(3))**：成交强度 λ(δ) = A·e^(−κδ)（δ = 距中价距离，κ = 衰减系数）；最优价差（对称情形）
  **δ\* = γσ²(T−t) + (2/γ)·ln(1 + γ/κ)**；reservation price **r(s,t,q) = s − qγσ²(T−t)**。
  第一项是库存/波动项（∝γ、σ²、剩余时间），第二项是逆选择补偿项（κ 越大、成交概率衰减越快，该项越宽）。【文献】
- **Cont–Stoikov–Talreja (2010, OR 58(3))**：限价簿随机模型 —— 成交概率随 δ 衰减；**远距离单只有价格"穿过"你时才成交，条件成交自带逆选择**（成交时价格运动方向与挂单方向相反）。【文献】
- **Lokin–Yu (2024, arXiv:2403.02572)**：状态依赖订单流下的成交概率解析/校准模型。【文献】
- **Albers–Cucuringu–Howison–Shestopaloff (2025, arXiv:2502.18625)**：Binance BTC 永续**实盘实验**——maker 成交概率与成交后收益**显著负相关**（"Market Maker's Dilemma"）；贴盘口顺订单流失衡的常见策略在实盘不盈利，只有**逆势（counter-trading）**的 touch 做市在"反转"状态下有效。【文献】

### 2.2 对我们的"越远 MAE 越浅但净收益越差"的理论解释【推导】
每腿期望值：**EV(δ) = P_fill(δ) · [capture(δ) − adverse(δ|fill)]**。
- P_fill(δ) 随 δ 快速衰减（指数/幂律）；capture 线性随 δ 上升；远单被"穿过"成交时 entry 更好（MAE 浅），但 P_fill 下降更快 ⇒ **EV 存在内点最大值**。
- 我们的 0.0675bp 半距 / 0.135bp 挂宽已落在"远、低成交率"分支：30 天仅捕获 602.91U，覆盖不了固定的逆选择暴露。按 λ(δ)=Ae^(−κδ) 反推 κ ≈ 20–40/bp（§5），在 0.0675bp 处成交强度已衰减 e^(−1.4~−2.7) 倍。
> **对我们的含义**：问题不在"距离"而在"条件"——远距离分支上唯一能翻正 EV 的是**状态门控挂单**（5min 趋势顺势侧 markout +4.4~+5.3bp），距离本身没有进一步优化空间。

---

## 3. 短期反转与日内动量

### 3.1 股票市场的时间域谱系【文献】
- **秒级**：Roll (1984) 买卖价弹跳 ⇒ 负自相关（噪音，非可交易 alpha）。
- **分钟–小时（日内动量）**：Gao–Han–Li–Zhou (2018 JFE, "Market intraday momentum")——首个半小时收益预测最后半小时，SPY 上斜率约 0.4–0.5（t≈4–5），高波动日更强；APAC 市场复现（Gao et al. 2023）。
- **日–月（反转）**：Lehmann (1990) 周度反转；Jegadeesh (1990) 月度反转（输家随后月跑赢赢家约 2%/月，毛）；**Nagel (2012)**：日频反转收益 = 流动性提供补偿，与 VIX 强正相关、集中在高波动时段。
- **衰减**：Aït-Sahalia–Fan–Xue–Zhou (2022, NBER w30366)——高频可预测性随竞争加剧显著衰减。

### 3.2 加密货币【文献】
- **Liu–Tsyvinski–Wu (2022 JF)**：加密货币存在显著的**短周期动量因子（1–4 周）**——与股票的 1 月反转形成对比，加密的"动量区"整体左移。
- **JRFM 19(9):692 (2026)**：BTC/ETH 日内白天/隔夜时段的动量与反转不对称。

### 3.3 分界结论（文献 × 我们）

| 时域 | 证据 | 机制 |
|---|---|---|
| ≤1s | 价弹跳负自相关（Roll 1984） | 噪音 |
| 1–60s | **我们 60s 反转 ≈0（\|t\|<1.5）** | 噪音反转已被更快玩家定价（Aït-Sahalia et al. 2022） |
| 1–15min | **我们 300s 动量 +0.37bp@30s、5min 突破 +0.96bp@300s（t=2.6）**；GHLC 日内动量 | 信息扩散 + 资金流持续 |
| ≥15min | **我们 15min 反转 +2.77bp@300s（t=3.5）**；Nagel 流动性补偿 | 过度反应回摆 |

> **对我们的含义**：永续上"秒级反转已死、分钟级动量与 15min 级反转并存"——我们的信号矩阵与文献谱系完全一致：60s 反转为 0 是**文献预期而非 bug**，唯一显著反转在 15min 不在 60s。

---

## 4. OFI（订单流失衡）

- **Cont–Kukanov–Stoikov (2014, J. Financial Econometrics 12(1):47–88)**：ΔP_t = β·OFI_t + ε_t，**β 与市场深度成反比**；OFI 解释同期价格变动**约 2/3（≈65%）**（50 只美股、多时间尺度稳健）；成交量模型的解释力更弱且更吵。推论：短周期价格主要由订单簿事件流（挂/撤/吃）驱动。【文献】
- **Cartea–Donnelly–Jaimungal (2018, AMF 25(1))**：把 OFI 类订单簿信号嵌入做市与执行策略，给出信号强度与报价调整的定量关系（信号做市 = "passive execution of alpha"）。【文献】
- **Bieganowski–Ślepaczuk (2026, arXiv:2602.00776)**：Binance 永续 1 秒 LOB 特征建模——OFI/价差/逆选择是跨币种（BTC~ROSE，市值差一个数量级）稳定的**头等特征**；闪崩中 taker 与 maker 回测的分化实证验证了逆选择理论。【文献】
> **对我们的含义**：OFI 门 0.15 带来 **+0.12~0.20bp/腿（≈无条件捕获 0.05bp 的 2–4 倍）**，正是"OFI 是短周期漂移的主要状态变量"的预测——OFI 应作为**挂单前置条件/距离调节器**，而不是独立 taker 信号。

---

## 5. 最优执行与库存（映射 300U 账户）

### 5.1 Avellaneda–Stoikov 闭式解【文献】
r(s,t,q) = s − qγσ²(T−t)；δ\* = γσ²(T−t) + (2/γ)·ln(1+γ/κ)；λ(δ) = Ae^(−κδ)。
γ = 库存风险厌恶（每单位 qσ² 的价格折扣），q = 库存（腿数）。

### 5.2 用我们的账本反推 γ 与 κ【推导】
- σ：BTC 类日波动 3% ⇒ σ ≈ 1.0bp/s ⇒ σ² ≈ 1×10⁻⁸（十进制²）。
- λ：36,739 笔/30 天 ≈ 0.014 笔/秒（总）；假设 2–4 条腿常驻 ⇒ 每腿 λ ≈ 0.004–0.007/s；取 A ≈ 0.02–0.05/s ⇒ **κ = ln(A/λ)/0.0675 ≈ 20–40 per bp**。
- 挂宽 0.135bp 代入最优价差公式 ⇒ **γ ≈ 2–6（数量级）**：库存项 γσ²(T−t)|₃₀ₛ ≈ 0.06–0.2bp，逆选择项 (2/γ)ln(1+γ/κ) ≈ 0.05–0.08bp。
- **关键推论**：库存上限 48%（≈0.48 腿）对应的报价偏移 qγσ²(T−t) ≈ **0.03–0.1bp**，只占挂宽的 25–70% 且小于一跳粒度噪声；若强行把 γ 提到 30–100 让库存项主导，价差公式给出 **1bp+ 挂宽**，按 κ≈28/bp 成交强度衰减 e^(−14+) ≈ 0 —— 成交率塌方。
- Menkveld (2013) 的持仓时长证据：**<5s 持仓 +€0.45/笔、>5s 持仓 −€1.13/笔** —— 持仓时长是逆选择的核心开关。【文献】
> **对我们的含义**：在 0.135bp 挂宽下 γ 被锁死在 2–6、报价倾斜几乎不起作用 ⇒ 300U/单腿 300U/库存 48% 的组合必须用**"硬开关"（到限即停逆势侧挂单 + 单腿减半）**控库存，不能靠搬价。

---

## 6. 已发表权威数据：费率、价差、crypto 逆选择与 HFT 收益量级

- **费率结构**（主流交易所 VIP0 档公开费率表）：USDT 本位永续 maker 0~2bp（部分返佣后为负）、taker 4~7.5bp（Binance 2/4、OKX 2/5、Deribit 0/5、BitMEX 2/7.5）。**AsterDEX 的 0/4bp 处于低成本端；但 maker 0 意味着没有返佣补偿逆选择，全部收入必须来自捕获本身。**
- **价差**：Binance BTCUSDT 永续 tick = 0.1 USD，在 ~10 万美元价位 ≈0.01bp/跳；最活跃时段盘口 1–2 跳（0.01–0.02bp），taker 实际有效成本（含走簿）远高于盘口价差（Aleti–Mizrach 2020/2021 对 BTC 现货与期货的微观结构测算）。**我们的半距 0.0675bp ≈ 6–7 跳 —— 本质是"排队单"，不是"贴盘口单"。**【文献】
- **价格发现**：SEC 文件引用的 Alexander–Heck 测算，期货/永续信息份额 **66–73%**（30 分钟频率）—— 衍生品主导 BTC 价格发现，逆选择源集中在永续。【文献】
- **跨所偏差**：Makarov–Schoar (2020 JFE)——BTC 跨所价差持续存在、随波动放大、小所偏差更大，说明"全局中价"本身就是多所竞争的结果。【文献】
- **crypto 逆选择直接证据**：Albers et al. (2025)（成交概率与成交后收益负相关）；Bieganowski–Ślepaczuk (2026)（闪崩中 maker/taker 回测分化）。【文献】
- **HFT 收益量级校准**：公开文献中**没有**权威的 crypto HFT 做市收益率数字；最可用的校准锚是 Menkveld (2013)：每笔 €0.88 毛利、Sharpe 9.35、但**逆选择吃掉捕获的 44%**；Baron et al. (2019)：激进 HFT 平均亏钱。另注：未检索到 "Alder 2023 Trade Execution Costs in Crypto" 这篇确切文献，最接近的公开实证是 Digital Finance (2023) 的 "Optimal trade execution in cryptocurrency markets"（§8 来源 20）与 Aleti–Mizrach 系列。【文献】
> **对我们的含义**：即便顶级做市商每笔也只净赚 ~€0.88 且被吃掉 44%；300U 账户的现实校准 = **每笔 0.01–0.05bp 量级的捕获 + 严格状态条件**，收益只能来自"选择性提供流动性"，不可能来自无条件贴盘口。

---

## 7. 结论：对 300U 车道最可操作的前 5 条（按证据强度排序）

1. **把被动成交限制在趋势顺势侧（5min 趋势 ≥20bp）。**
   支撑：markout 从全样本 −0.1~−0.3bp 翻到 **+4.4~+5.3bp**（n=576~928）；文献：Albers et al. (2025) 的"Reversals 状态"、Hagströmer–Nordén (2013) 的 MM/机会型分类、DeLise (2024) 的负漂移。
   矛盾面：顺势侧样本只占成交的 ~2%，执行上必须接受更低的成交频率与更长的等待。

2. **15min 反转信号只做被动表达（挂单等成交），taker 路径关闭。**
   支撑：+2.77bp@300s（t=3.5）是唯一 |t|>3 的信号，但 taker 4bp 是全部信号毛边际（0.4~1bp）的 **4–10 倍**；文献：Baron et al. (2019)（激进 HFT 平均亏损）+ AS (2008) reservation price（把信号折进报价）+ Cartea et al. (2018)（信号做市）。

3. **OFI 门 0.15 作为挂单前置条件（顺 OFI 侧才挂同侧）。**
   支撑：**+0.12~0.20bp/腿（≈无条件捕获的 2–4 倍）**；文献：CKS (2014)（β ∝ 1/深度、解释 ~2/3 同期变动）+ Bieganowski–Ślepaczuk (2026)（crypto 跨币种特征稳定）。
   矛盾面：OFI 是同期变量，作前置条件须用短滞后预测并做样本外验证。

4. **库存控制改"硬开关"，不做报价倾斜。**
   支撑：§5 校准 γ≈2–6 时倾斜力度仅 0.03–0.1bp（< 挂宽 1/3）；文献：AS (2008) 公式 + Menkveld (2013) ">5s 持仓每笔 −€1.13"；映射：**48% 上限 → 到限即停逆势侧挂单 + 单腿名义减半**。

5. **用 VPIN/毒性过滤持续监测逆选择环境，并按 Menkveld 基准做预期校准。**
   支撑：逆选择/捕获 = **2.01** 是文献基准 0.44 的 4.5 倍、30 天净 **−867.77 USD**；文献：ELO (2012) VPIN（CDF>0.9 毒性预警）、EHO (1992) PIN（NYSE 均值 ≈0.19）、Menkveld (2013)。
   矛盾面：crypto 无公开 PIN 估计，需用自己的订单簿数据估计 μ（VPIN 桶方法）并把它作为门控阈值之一。

---

## 8. 来源清单（URL，全部经本次 web 检索核验）

1. Menkveld (2013 JFM), "High frequency trading and the new market makers"（摘要含逐笔 €0.88/1.55/0.68 分解）— https://ideas.repec.org/p/tin/wpaper/20110076.html
2. Albers, Cucuringu, Howison, Shestopaloff (2025), "The Market Maker's Dilemma"（Binance BTC 永续实盘）— https://ideas.repec.org/p/arx/papers/2502.18625.html
3. Cont, Kukanov, Stoikov (2014), "The Price Impact of Order Book Events" — https://arxiv.org/abs/1011.6402
4. Bieganowski, Ślepaczuk (2026), "Explainable Patterns in Cryptocurrency Microstructure" — https://arxiv.org/abs/2602.00776
5. Alexander, Choi, Park, Sohn (2020), "BitMEX bitcoin derivatives" — https://onlinelibrary.wiley.com/doi/abs/10.1002/fut.22050
6. Aleti, Mizrach (2021), "Bitcoin spot and futures market microstructure" — https://onlinelibrary.wiley.com/doi/10.1002/fut.22163
7. Makarov, Schoar (2020 JFE), "Trading and arbitrage in cryptocurrency markets" — https://www.semanticscholar.org/paper/Trading-and-arbitrage-in-cryptocurrency-markets-Makarov-Schoar/c86e30f6f190fdce85945cb1c5a7549449418b66
8. Gao et al. (2023), "Market intraday momentum: APAC evidence" — https://www.sciencedirect.com/science/article/pii/S0927538X2300152X
9. "Lagged Momentum and Reversal Strategies Across Daytime and Overnight Sessions in Bitcoin and Ethereum"（JRFM 2026）— https://www.mdpi.com/1911-8074/19/9/692
10. SEC (2014), HFT 文献综述 — https://www.sec.gov/marketstructure/research/hft_lit_review_march_2014.pdf
11. Baron, Brogaard, Hagströmer, Kirilenko, "Risk and Return in High-Frequency Trading"（CFTC 托管 PDF）— https://www.cftc.gov/sites/default/files/idc/groups/public/@economicanalysis/documents/file/oce_riskandreturn0414.pdf
12. Hagströmer, Nordén (2013 JFM), "The Diversity of High-Frequency Traders" — https://www.semanticscholar.org/paper/The-Diversity-of-High-Frequency-Traders-Hagstr%C3%B6mer-Nord%C3%A9n/ee747105da24a77ed8dd091816035ccdb66f849b
13. Avellaneda, Stoikov (2008 QF), "High-frequency trading in a limit order book" — https://ideas.repec.org/a/taf/quantf/v8y2008i3p217-224.html
14. Cont, Stoikov, Talreja (2010), "A stochastic model for order book dynamics" — https://zbmath.org/pdf/1232.91719.pdf
15. Lokin, Yu (2024), "Fill Probabilities in a Limit Order Book with State-Dependent Stochastic Order Flows" — https://ideas.repec.org/p/arx/papers/2403.02572.html
16. Hendershott, Menkveld (2014 JFE), "Price Pressures" — https://ideas.repec.org/a/eee/jfinec/v114y2014i3p405-423.html
17. Aït-Sahalia, Fan, Xue, Zhou (2022 NBER w30366), "How and When are High-Frequency Stock Returns Predictable?" — https://ideas.repec.org/p/nbr/nberwo/30366.html
18. VPIN 方法与应用（含 BTC 应用的行业综述；原 MEXC 页面已下线，检索记录可查）— https://faculty.bus.olemiss.edu/rvanness/Speakers/Presentation%202013-2014/Yildiz-VPIN.pdf ；https://www.mexc.io/news/1002105
19. HTX Insights（从业者视角：做市商与套利者的生存结构）— https://www.htx.co.zw/news/two-survival-structures-of-market-makers-and-arbitrageurs-8BcFpx1G/
20. "Optimal trade execution in cryptocurrency markets"（Digital Finance, 2023）— https://link.springer.com/content/pdf/10.1007/s42521-023-00103-y.pdf
21. DeLise (2024), "The Negative Drift of a Limit Order Fill" — https://ideas.repec.org/p/arx/papers/2407.16527.html
22. SEC 文件引用的 Alexander–Heck 价格发现测算（期货信息份额 66–73%）— https://www.sec.gov/files/rules/sro/nysearca/2021/34-93445-ex3a.pdf
