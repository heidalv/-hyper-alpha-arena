# -*- coding: utf-8 -*-
"""[h835] 记录:停产诊断 + 冷启动探索通道上线。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\流交易策略体系总体设计_20261004.md")
s = p.read_text(encoding="utf-8")
add = """

## 14. "真没有交易了"的诊断与冷启动通道(10-04 23:1x)

### 14.1 逐层诊断(1→5)
```
1) 市场数据 ✓ 正常(最新成交 1~2 秒前,近 5 分钟 1100 行)
2) worker ✓ 正常(状态文件 0 秒前更新,ticks 递增)
3) 四层门:
   situation(情况表,32 币/99 桶)→ **只有 2 个桶**满足 mean_y>1bp 且 n≥30
   gate(queue_cleared_exam,34 币)→ 0 开(且文件 2.5h 未更新,>30min max_age)
   layered(候选)→ 1/12
4) 成交:近 3 小时只有 2 腿(22:55 BTW 一对)
5) 阻断点:situation_not_this_tick ≈ 99.9% 的 tick
```
**结论:不是"完全没有交易",是被第四层门掐到近乎为零 —— 而根因是结构性死锁:
桶要 30 笔完整来回才有资格判,而没证据就不交易 ⇒ 桶永远填不满(不可 falsify)。**

### 14.2 解法:冷启动探索通道(h834 上线)
- `flow_rules.situation_decision(..., probe_ok=True)`:没有已证实桶时,
  允许一笔**极小名义**探索单(reason=`situation_probe`,`probe=True`);
- runner:每币每小时最多 `MM_FLOW_PROBE_PER_HOUR`(默认 2)笔探索,台账 `_PROBE_LOG`;
- 名义 = `equity × PROBE_EQUITY_FRAC`(5%)≈ $7.5 —— **只有探索单才压到极小,
  已证实的桶仍走正常名义上限**;
- `active_flow`:探索单豁免"mu > 安全垫"检查(probe 的 mu 定义为 0,否则被
  `mu_below_margin` 挡掉 = 死锁无解);探索单进往返日志,不冒充已证实证据。

### 14.3 首批探索成交(23:09-23:12)
```
23:09:21 SI flow_entry_maker +76.9bp 名义 7.50U   ← 探索单(5% 权益)
23:12:05 SI taker_stop      −143.2bp 名义 7.45U   ← 灾难止损,亏损 ≈ $0.11
```
**风险边界**:单笔最大亏损 ≈ $7.5 × (止损+滑点) ≈ $0.1~0.3;每币每小时 ≤2 笔。
桶填满(≥30 笔来回且均值为正)后,该桶自动以正常名义开门 —— 死锁解除。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h835")
