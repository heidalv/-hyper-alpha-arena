# 宇宙 v5 新币微试跑协议(预注册,h652,2026-09-30)

> 隶属《宇宙替换分析v5设计_20260930.md》L4。新进币/历史差评回归币不直接上位,
> 必须先过 12h 单变量微试跑(h356/h357 同款口径)。
> 首轮对象(16:55 实际换币,滚动窗口动态更新):**新币 ENA、SUI**(宇宙 [BNB,XRP,SUI,ENA],
> 旧宇宙 [ETH,BNB,BTC,XRP];被替换 = ETH/BTC)。
> 执行记录:16:54 D1 提前切窗判决 KEEP → 16:55 换币(单变量:未写 pattern_matrix,矩阵留待
> 宇宙判决后的独立试跑)→ trial_active 重新上锁(宇宙微试跑)。

## 1. 假设

ENA(事件研究 max edge 3.01,P1/P5)与 UNI(2.85,P4/P5)在现行策略(趋势闸/方向卡/
形态闸/出场栈)下,12h 微试跑的逐腿净 bp 为正或显著优于被替换币的同期基线。

## 2. 变更(单变量)

| 项 | 值 |
|---|---|
| 变更 | 宇宙替换:提案 4 币,其中 ENA/UNI 为新进(首次试跑最多换 2 = max_new,天然对齐) |
| 其余 | 全部按住:D1 判决后的最终参数族、方向卡、出场栈、腿量不动 |
| 入口 | `python scripts/h329_selector_v5.py --apply --lane mm_asterdex`(自动写 h329v5_rollback) |
| 回滚 | `meta.h329v5_rollback.symbols` 恢复;或 `mm_set_symbols.py` 写回旧宇宙 |

## 3. 判决口径(预注册,12h)

- 窗口:**12 小时**自 apply 生效(热采用 ≤60s);
- 分组:新币(ENA/UNI)的往返逐腿净 bp(quote-time 归因,移动库存法)vs
  **被替换币(ETH/BTC)在换币前最后 12h 的同期口径**;
- 指标:①新币逐腿净 bp 的 90% CI 上界 > 0 或 > 被替换币基线(双侧 Welch α=0.10);
  ②全车道腿数 ≥60/h 不塌;③day_pnl 12h 累计 > 换币前 12h 累计(或 ① 达标即可保留新币);
- 裁决:
  - A. 腿数塌陷(<0.8×基线)⇒ 回滚;
  - B. 新币逐腿 bp 显著更差且 ① 不达标 ⇒ 回滚该币(回旧宇宙或换候补 ARB/DOGE/SUI);
  - C. ① 达标 ⇒ 保留新币,写入正式槽;
  - D. 不显著 ⇒ 延长 12h 观察。
- 判决文件:`research_l1/out/h652_universe_trial_verdict.json`;
  试跑期间 `trial_active=true`(宇宙/参数互斥锁)。

## 4. 前提(23:50 判决链)

1. D1 判决完成(trend_pause_bp 保留或回滚均已落账);
2. `trial_lock_set.py --off --reason "D1 判决完成"`;
3. `schtasks /Change /TN DSH_HFT_UNIVERSE_SELECT /ENABLE`;
4. 跑 `h329_selector_v5.py --apply`(此刻宇宙生效即微试跑起点);
5. 设 12h 后的宇宙判决提醒(同 23:50 链模式)。
