# -*- coding: utf-8 -*-
"""[h784] 执行记录:崩盘熔断(秒级隔离连续跳空的崩盘币)。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\方向判定改造执行记录_20260930.md")
s = p.read_text(encoding="utf-8")
add = """

### 六bk、崩盘熔断:连续跳空币的秒级隔离(h784,10-04 03:0x)

**02:00 时段现场**:SI 深夜连环崩 —— 02:08 jump −191bp / 02:32 jump −257bp /
02:46 stop −280bp / 02:46 stop −274bp / 02:50 jump −145bp,5 条合计约 −$2.9U,
当小时 83 腿净 −3.60U 的主要来源。硬上限(≤$78)把每条压到了 $0.2~1.0 级
(旧结构下这种连环崩会是 −$5~10 级),但**没有机制在第一次跳空后停掉这个币**:
亏损淘汰(pnl_decayed)要 4h 窗口 ≥30 腿,对 5 腿崩盘的币太慢。

**新增(h784,焊进 runner)**:
- `_record_fills`:平仓腿价格项 < −150bp ⇒ `_crash_block[symbol] = now+1800`
  + 事件推送"崩盘熔断:XX −XXXbp ⇒ 30 分钟只减不加";
- `_refresh_decay_block`(每 60s):把仍在隔离期的 crash_block 并入实时淘汰集合
  ⇒ 该币**下一分钟起**只减不加(与 h759 快路同一通道);
- 隔离 30 分钟后自动解除(可被下一次崩盘再次触发)。

**语义**:跳空本身无法预防(速退/止损都在跳空价成交),能防的是**第二、三、
四刀** —— 第一次跳空后立刻停手,不再继续接刀。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h784")
