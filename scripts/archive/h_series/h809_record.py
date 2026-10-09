# -*- coding: utf-8 -*-
"""[h809] 记录:模型门整合 + 三条路全面执行的结论。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\流交易策略体系总体设计_20261004.md")
s = p.read_text(encoding="utf-8")
add = """

## 8. 全面执行记录:三条路 + 公式设计与算法整合(10-04 16:xx)

**用户指令:"全面执行,并且通过数据和过去的经验进行公式设计和算法整合。"**

### 8.1 三条路全部执行(每条都是 walk-forward 样本外)

| 路 | 做法 | 样本外结果 |
|---|---|---|
| ① 细时限 | 5s / 15s / 30s / 90s 前向漂移(6 宇宙币) | **0 笔入选**(预测漂移从未达 6bp 成本) |
| ② 相对价值 | 币 vs BTC 相对偏离 + 相对流(12 维特征) | 绝对/相对 **0 笔入选** |
| ③ 真深度 OFI | asterdex_depth_snapshots 20 档快照 → 深度失衡 + 最优档 Δ(Cont/Kukanov/Stoikov 口径) | 仍 **0 笔入选**;校准后 P≥0.55 的选中 CBRS 168 笔 **−5.97bp** |

### 8.2 公式设计(每条注明来源)

```
特征(12 维):rel_mid_bp(相对价值) / ret_60s,ret_300s(动量) / ref_ret_60s /
  dep_imb, dep_imb_chg, top_imb, ofi_depth(真深度 OFI)/ ofi_60s, ofi_60n /
  spread_bp / vol_300s
模型:HistGradientBoosting(回归 E[漂移] + 分类 P(方向)+ isotonic 校准)
评估:walk-forward 前 2/3 训练 / 后 1/3 样本外;Brier 报校准度
评分规则:pred_bp ≥ 成本(6bp = 价差 2 + taker 费 4)才进场
```

### 8.3 算法整合(把证据接进引擎)

`scripts/h809_flow_gate.py`(每 15 分钟)对每币重训模型、预测当前 90s 期望漂移,
写 `data/flow_gate_last.json`;runner 的 active_flow 分支读它:

- **pred ≥ 6bp ⇒ 放行进场;否则不进场**(只允许既有仓离场)。
- 首轮实测(10h 数据):6 币预测 −1.25 ~ +3.02bp ⇒ **全部门关**(0/6),
  车道 skip = `model_gate_no_edge` 1336 次 ⇒ **没有证据就不交易**。

**这是"准确的代价":车道现在几乎不交易(腿速降到 ~12/h,只剩离场),
因为所有可测的分辨率下都没有覆盖成本的 edge。门每 15 分钟重算,
数据积累到出现正期望组合时自动放行 —— 学习不停,但不再用噪音交易烧钱。**
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h809")
