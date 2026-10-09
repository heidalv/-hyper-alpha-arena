# -*- coding: utf-8 -*-
"""[h795] 执行记录:三层对齐 30s-5min 超短范式。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\方向判定改造执行记录_20260930.md")
s = p.read_text(encoding="utf-8")
add = """

### 六br、三层对齐 30s-5min 超短范式(h795,10-04 13:2x)

用户追问:"策略、平仓机制、概率分析这些都不修改么?"—— 底层方向纠正后,
上面三层必须一起对齐。逐层核对与处置:

**概率分析层**:方向卡(每 5 分钟用最近 60 分钟**开仓腿实现 markout** 重判
每币方向,封住坏侧)保留 —— 这就是"概率"的实证形态;h794 修复后它不再被
软模式覆盖,真正生效(待 side_one 验证)。前瞻信号(OFI/流)继续由流闸族
(pullback/squeeze/breakout)供给,与方向卡组成"前瞻流+后验制裁"。

**策略层**:不再全时段双边做市 —— 只挂流对齐侧(h794)+ 报价贴盘口不插价差
(h793)。持仓形态映射 p1_hold_sec=60 / p45_hold_sec=300 已在 30s-5min 窗内 ✓。

**平仓机制层(本次执行)**:
| 参数 | 旧 | 新 | 语义 |
|---|---|---|---|
| timeout_hard_taker_sec | 600 | **300** | 持仓硬顶 = 5 分钟(范式外沿) |
| trend_flip_flatten_bp | 0 | **12** | 300s 趋势逆势 12bp ⇒ **信号翻转即离场** |
| reduce_touch_after_sec | 300 | **180** | 3 分钟起减仓侧贴盘口 maker 离场 |
| take_profit_bp | 60 | 60 | 保持(短平快) |
| stop_loss_bp | 40 | 40 | 保持 |

三项均以 exit_cost 判据登记 30 分钟快判。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h795")
