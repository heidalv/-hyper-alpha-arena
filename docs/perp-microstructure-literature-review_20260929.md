# 加密货币永续合约微观结构与「错价度量」文献调研

调研日期：2026-09（依据 web_search + web_fetch；所有条目均经 Crossref / OpenAlex / RePEc / arXiv / 出版社页面核实，未核实细节已注明）。行业报告单独标注。

设计映射说明：以下映射到你们错价分数的五个特征——**资金费率 z-score（多头拥挤度）**、**清算簇/爆仓磁铁**、**OI 变化**、**taker 流不平衡/CVD**、**现货-永续基差**（§5.2/§5.3 的具体编号以你们文档为准）。

---

## 1. 资金费率（funding rate）研究

- **Fundamentals of Perpetual Futures**（Songrun He, Asaf Manela, Omri Ross, Victor von Wachter, 2022，arXiv v7 2026）—— 推导永续合约无套利价格与交易成本边界：funding 与 premium 挂钩；perp-现货偏离在加密市场远大于传统外汇、跨币种联动、随时间衰减；隐含套利策略高 Sharpe。资金费率/基差偏离无套利边界即「错价」的理论锚。映射：资金费率 z-score + 基差。https://arxiv.org/abs/2212.06888
- **Crypto Carry**（Maik Schmeling, Andreas Schrimpf, Karamfil Todorov, 2023，BIS Working Paper 1087）—— 加密期货-现货基差（carry，年化）可超过 **40%**；由散户趋势追逐者的杠杆需求 + 套利资本部署受限（监管/保证金摩擦）共同驱动；现货 BTC ETF 推出后各交易所基差显著压缩。映射：基差 + 资金费率（拥挤度成因）。https://www.bis.org/publ/work1087.htm
- **Perpetual Futures as Predictors of Bitcoin Volatility**（Da-Hea Kim, 2026，Journal of Empirical Finance）—— 永续合约衍生变量对 BTC 波动率具有样本外预测信息（标题层面主张；全文订阅制，细节需自行核对）。映射：资金费率 z-score。https://doi.org/10.1016/j.jempfin.2026.101779
- **Futures As Prelude: Bitcoin Price Forecasting From Perpetual Futures Data**（Brian Kachnowski, 2022，SSRN 4097789，工作论文）—— 用永续合约数据（资金费率/基差等）预测 BTC 现货价格；未正式发表，引用时标注工作论文并自验方法。映射：资金费率 z-score。https://doi.org/10.2139/ssrn.4097789
- **The Two-Tiered Structure of Cryptocurrency Funding Rate Markets**（Petar Zhivkov, 2026，Mathematics 14(2):346）—— 35.7M 条一分钟观测、26 交易所、749 交易对：永续合约占加密交易量约 **93%**；CEX 价格发现整合度比 DEX 高 **61%**，信息流单向 CEX→DEX；**17%** 观测存在 ≥20bp 套利价差，但仅 **40%** 的头部机会事后盈利、**95%** 最终被迫平仓。映射：资金费率 z-score + 基差（错价普遍存在但难交易）。https://doi.org/10.3390/math14020346
- **Failure of Cross-Sectional Alpha Screening on Cryptocurrency Perpetual Futures: A Quantitative Post-Mortem Using OHLCV and Funding Rate Signals**（Azka Fayez Junior, 2026，SSRN 6701738，工作论文）—— 负面结果：用 OHLCV + 资金费率信号做横截面 alpha 筛选失败。对你们设计的警示：资金费率更适合做条件/时序信号而非 naive 横截面选币。映射：资金费率 z-score（设计校准）。https://doi.org/10.2139/ssrn.6701738
- **行业报告（注明）**：K33 研究报告（经 The Block 2025 转载）—— BTC 出现十年来最长负资金费率连续期，提示空头挤压（short squeeze）风险，即「负费率极端 → 上行反转」的行业侧证据。https://www.theblock.co/post/400182/bitcoin-longest-negative-funding-streak-this-decade-k33-flags-short-squeeze-risk ；VanEck（Bitcoin Magazine 转载）—— 资金费率转负 + 哈希率下滑被视为双重看涨信号。http://bitcoinmagazine.com/news/vaneck-flags-dual-bullish-for-bitcoin

## 2. 清算级联（liquidation cascade）研究

- **Towards Understanding Cryptocurrency Derivatives: A Case Study of BitMEX**（Kyle Soska, Jin-Dong Dong, Alex Khodaverdian, Ariel Zetlin-Jones, Bryan Routledge, Nicolas Christin, 2021，WWW '21，ACM）—— BitMEX 日均交易超 **$30 亿**、最高 **100 倍**杠杆；基于公开强平事件数据分析衍生品对标的现货价格的影响（强平引擎是价格冲击的实证来源）。映射：清算簇。https://doi.org/10.1145/3442381.3450059
- **Where does the criticality live? Early-warning signals are event-heterogeneous across seven crypto-perpetual liquidation cascades**（Ramon Marc Garcia Seuma, 2026a，arXiv 2607.27070）—— 七次 BTC 清算级联（2022–2025，含 2025-10-10 史上最大、约 **$190 亿**事件）：临界慢化信号**事件间异质**（内生积聚 vs 外生冲击两类）；唯一全事件成立的前兆是 **taker 订单流方差压缩**。映射：清算簇 + taker 流不平衡/CVD。https://arxiv.org/abs/2607.27070
- **Measuring the engine of a liquidation cascade: subcritical branching inside a first-order transition**（Ramon Marc Garcia Seuma, 2026b，arXiv 2608.03616）—— 对 2025-10 级联的链上逐笔核算：级联分支比 **λ≈0.1–0.2（亚临界，非临界相变）**；**88%** 的强制卖出发生在起爆后 30 分钟内；**63%** 被场外后备流动性吸收；级联中 **OI 清退 25–70%**、价格冲击尖峰出现在流动性端。映射：清算簇 + OI 变化。https://arxiv.org/abs/2608.03616
- **Same Shock, Same Assets, Different Microstructure: A Comparative Analysis of CeFi and DeFi Venue Performance During the October 10, 2025 Cryptocurrency Cascade**（B.C. Lim, 2026，Research Square 预印本）—— 同一冲击下 CeFi 与 DeFi 永续场所表现差异的微观结构解释。映射：清算簇。https://doi.org/10.21203/rs.3.rs-9459584/v1
- **Autodeleveraging as Online Learning**（Tarun Chitra, Nagu Thogiti, Mauricio Ramirez, Victor Xu, 2026，arXiv 2602.15182）—— 把 ADL（自动减仓）形式化为在线学习：2025-10-10 Hyperliquid 压力事件中，生产级 ADL 队列**过度清算盈利账户达 $5170 万**（最优算法可将溢出降至约 $300 万）。映射：清算簇（清算机制本身的摩擦成本）。https://arxiv.org/abs/2602.15182
- **Replication Materials for a Study of Collateral Architecture, Leverage Choice, and Liquidation Paths in Perpetual Futures**（Kyoungyoung Jang, 2026，Zenodo 数据集/复现包）—— Hyperliquid 2025-10-10/11 级联的账户级重建：**223,034 个仓位、11,474 个被强平**；对比全仓/逐仓架构的清算路径与清算后延续行为。映射：清算簇（爆仓价磁铁可操作性的微观证据来源）。https://doi.org/10.5281/zenodo.21508122
- **行业报告（注明）**：CoinGlass Liquidation Heatmap——「爆仓价磁铁」概念的行业实践来源（清算簇热力图的定义与可视化方法论；学术上对磁铁效应的直接因果实证仍薄弱，Garcia Seuma 两篇是目前最接近的机制证据）。https://www.coinglass.com/pro/futures/LiquidationHeatMap

## 3. OI / taker 流 / CVD（order flow）研究

- **Order Flow and Cryptocurrency Returns**（Alexia Anastasopoulos, Nikola Gradojevic, Fred Liu, Alex Maynard, Ilias Tsiakas, 2026，Journal of Financial Markets 79）—— 11 种计价货币的国际订单流对加密收益**横截面**有强解释力与预测力，样本外胜过基本面，且无法用套利限制解释（存在永久价格效应）。映射：taker 流不平衡/CVD。https://doi.org/10.1016/j.finmar.2026.101047
- **Fragmentation, Price Formation and Cross-Impact in Bitcoin Markets**（Jakob Albers, Mihai Cucuringu, Sam Howison, Alexander Y. Shestopaloff, 2022，Applied Mathematical Finance）—— BTC 市场碎片化下的价格形成：主动（taker）流对价格的冲击跨交易所传导（cross-impact）。映射：taker 流不平衡/CVD。https://doi.org/10.1080/1350486X.2022.2080083
- **Bitcoin Spot and Futures Market Microstructure**（Saketh Aleti, Bruce Mizrach, 2021，Journal of Futures Markets 41(2):194–225）—— CME 期货**领先价格发现**；现货中位成交 <$1,300 vs CME >$18,000；trade-through 估计损失 $36M。映射：taker 流不平衡/CVD + 基差（价格发现层级）。https://doi.org/10.1002/fut.22163
- **Reconciling Open Interest with Traded Volume in Perpetual Swaps**（Ioannis Giagkiozis, Emilio Said, 2024，Ledger 9）—— 七家头部衍生品交易所 tick 级对账：BTC 永续 **OI 被部分大所系统性错报**，且强平（forced trade）消息存在延迟。**对 OI 特征的直接数据质量警示**。映射：OI 变化（必须做数据清洗/对账）。https://arxiv.org/abs/2310.14973
- **行业报告（注明）**：Amberdata, *Leveraging Order Flow and Order Book Heatmaps for Market Trend Analysis*——taker 流与订单簿热力图（CVD/流不平衡）的行业方法论文档。http://go.amberdata.io/hubfs/Leveraging%20Order_Flow%20and%20Order_Book%20Heatmaps%20for%20Market%20Trend%20Analysis.pdf ；Amberdata Funding Rates 数据字典（funding 字段口径参考）。https://docs.amberdata.io/data-dictionary/market/funding-rates

## 4. 基差 / 现货-永续价差（basis）研究

- **Crypto Carry**（Schmeling, Schrimpf, Todorov, 2023，BIS WP 1087）—— 见 §1：carry 超 40% 年化、ETF 后基差压缩；基差=「不便收益（convenience yield）」+ 套利摩擦。映射：基差。https://www.bis.org/publ/work1087.htm
- **Fundamentals of Perpetual Futures**（He, Manela, Ross, von Wachter, 2022）—— 见 §1：perp 与现货的无套利价差边界，偏离即错价；偏离跨币种联动、随时间衰减（市场成熟过程）。映射：基差。https://arxiv.org/abs/2212.06888
- **Trading and Arbitrage in Cryptocurrency Markets**（Igor Makarov, Antoinette Schoar, 2020，Journal of Financial Economics 135(2):293–319）—— 跨交易所价差持续存在，国家间价差大于国内且更持久；套利资本部署受限是价差无法收敛的主因。映射：基差（跨市场价差特征）。https://ideas.repec.org/a/eee/jfinec/v135y2020i2p293-319.html
- **BitMEX Bitcoin Derivatives: Price Discovery, Informational Efficiency, and Hedging Effectiveness**（Carol Alexander, Jaehyuk Choi, Heungju Park, Sungbin Sohn, 2020，Journal of Futures Markets 40(1):23–43）—— BitMEX 衍生品**领先**现货价格发现、信息效率更高、可对冲现货波动（基差风险的直接测度）。映射：基差 + 资金费率。https://doi.org/10.1002/fut.22050
- **What Can We Learn from the Convenience Yield of Bitcoin? Evidence from the COVID-19 Crisis**（Gideon Bruce Arkorful, Haiqiang Chen, Ming Gu, Xiaoqun Liu, 2023，International Review of Economics & Finance 88:141–153）—— BTC 基差中的便利收益在危机期（COVID）的行为——基差极端值携带状态信息。映射：基差。https://ideas.repec.org/a/eee/reveco/v88y2023icp141-153.html
- **Perpetual Futures and Basis Risk**（AEA 2026 Annual Meeting 会议论文）—— perp 基差风险的均衡建模（会议页面抓取超时，作者与结论以 AEA 程序为准）。映射：基差。https://www.aeaweb.org/conference/2026/program/paper/ByyFEfr4

## 5. 加密市场异象 / 可预测性

- **Risks and Returns of Cryptocurrency**（Yukun Liu, Aleh Tsyvinski, 2021，Review of Financial Studies 34(6):2689–2727）—— 加密收益暴露于市场、规模、动量三个加密特定因子；投资者关注度（搜索量）可预测收益；与传统资产因子暴露弱。映射：错价分数的横截面先验（动量/反转）。https://ideas.repec.org/a/oup/rfinst/v34y2021i6p2689-2727..html
- **Common Risk Factors in Cryptocurrency**（Yukun Liu, Aleh Tsyvinski, Xi Wu, 2022，Journal of Finance 77(2):1133–1177）—— 市场、规模、动量三因子模型解释加密横截面收益；九个价格/市场类因子多空策略超额收益显著，全部被三因子模型吸收。映射：OI 变化/taker 流作为增量因子的基准。https://doi.org/10.1111/jofi.13119
- **Crypto Value, Factor Pricing, and Market Segmentation**（Lin William Cong, G. Andrew Karolyi, Ke Tang, Weiyi Zhao, 2026，Management Science；SSRN 2023 版题为 *Value Premium in Cryptocurrency Markets*）—— 加密市场存在显著价值溢价，且上市渠道/市场分割（DeFi vs CeFi）影响横截面定价。映射：横截面错价排序的因子基准。https://doi.org/10.1287/mnsc.2024.05875
- **Cryptocurrency Momentum and Reversal**（Victoria Dobrynskaya, 2023，Journal of Alternative Investments；SSRN 2021）—— 加密货币同时存在**短期（约 1 周）反转**与**中期（1–4 周）动量**，符号相反、均显著——错价分数若以动量/反转逻辑兑现收益，必须按持有期分层。映射：资金费率 z-score/OI 变化的信号兑现周期设计。https://doi.org/10.3905/jai.2023.1.189
- **Predictive Sorting of Cryptocurrencies Based on Fundamentals and Sentiment**（Massimo Guidolin, Serena Ionta, 2026，Journal of International Financial Markets, Institutions and Money 107）—— 基本面与情绪指标对加密资产的预测性排序。映射：错价分数中加入基本面/情绪正交化。https://ideas.repec.org/a/eee/intfin/v107y2026ics1042443126000016.html

---

## 综合发现（可直接用于设计的 5 条）

1. **资金费率/基差极端 = 拥挤度温度计，但信号是条件性的**。理论锚（He et al.）是「perp 偏离无套利边界」；BIS 显示 carry 可超 40% 年化、由杠杆需求与套利摩擦驱动；K33/VanEck 的行业证据显示负费率极端 → 空头挤压。但 Garcia Seuma（2026a）证明级联前兆**事件间异质**（内生积聚 vs 外生冲击两类），因此 funding z-score 应作为**条件信号 + 状态分类器**，而非独立买卖触发器。
2. **清算级联是「亚临界」现象，冲击集中在时间与流动性端**。分支比 λ≈0.1–0.2（非临界）、88% 强制卖出落在 30 分钟窗口、63% 被场外吸收、OI 清退 25–70%。→ 清算簇特征应编码「流动性错位 + 时间窗 + OI 清退速率」，而非简单清算密度；「爆仓价磁铁」的直接学术实证仍薄弱（主要依据行业实践 CoinGlass 热力图），设计时应把它作为先验而不是已验证的因子。
3. **taker 流不平衡有最强的学术支持**（JFM 2026：订单流对加密横截面收益有解释/预测力且无法用套利限制解释；Albers et al. 的跨交易所冲击传导）。但 **OI 数据不可直接信**：Giagkiozis & Said（Ledger 2024）证明部分大所系统性错报 OI、强平消息延迟——你们 27.7M 行 funding / 2.5M 行成交流在入模前应做 tick 对账式质量过滤（其方法可直接复用）。
4. **错价大≠错价可交易**。Zhivkov（2026）：17% 观测存在 ≥20bp 套利价差，但仅 40% 机会事后盈利、95% 被迫平仓。→ 错价分数应区分「结构性错价」（保证金/监管摩擦所致，持久但不可交易）与「可交易错价」（会均值回归），并把执行成本/被迫平仓风险纳入分数或下游风控。
5. **横截面因子在加密市场成立，但 perp 特有信号别做成 naive alpha**。Liu-Tsyvinski-Wu 三因子（市场/规模/动量）、Cong et al. 价值因子、Dobrynskaya 周反转+月动量都稳健；然而 Fayez Junior（2026）的负面结果直接显示：OHLCV+funding 的横截面 alpha 筛选会失败。→ 建议错价分数优先用于**单标的时序/条件信号（择时、仓位缩放、风险开关）**，横截面用法需先用三因子基准做增量检验。

---

### 附：可引用数字速查

| 数字 | 来源 |
|---|---|
| 加密 carry（年化基差）可超 40% | Schmeling-Schrimpf-Todorov (2023, BIS WP 1087) |
| 永续合约 ≈ 加密交易量 93% | Zhivkov (2026) |
| ≥20bp 套利价差占观测 17%；事后盈利仅 40%；95% 被迫平仓 | Zhivkov (2026) |
| 2025-10-10 级联为史上最大（≈$190 亿事件）；级联分支比 0.1–0.2；88% 强制卖出在 30 分钟内；63% 场外吸收；OI 清退 25–70% | Garcia Seuma (2026a/2026b, arXiv 2607.27070 / 2608.03616) |
| BitMEX 日均 >$30 亿、最高 100x 杠杆 | Soska et al. (2021, WWW) |
| Hyperliquid ADL 过度清算盈利账户 $5170 万（最优算法 $300 万） | Chitra et al. (2026) |
| Hyperliquid 级联 223,034 仓位中 11,474 被强平 | Jang (2026, Zenodo 复现包) |
| CME 期货领先价格发现；现货中位成交 <$1,300 | Aleti & Mizrach (2021, JFM) |
| 9 个加密因子多空策略超额收益显著，均被三因子模型解释 | Liu-Tsyvinski-Wu (2022, JF) |
| 十年最长负资金费率连续期 → 空头挤压风险（2025，行业） | K33 经 The Block |
