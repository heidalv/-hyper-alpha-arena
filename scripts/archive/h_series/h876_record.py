# -*- coding: utf-8 -*-
"""[h876] 记录:止盈层补上(设计里有 tp_bp=60,阶梯从未实现)。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\Aster专属交易规则集_20261005.md")
s = p.read_text(encoding="utf-8")
add = """

## 7. 桥 12:58 + 止盈层补上(h876)

### 7.1 发现:挂单 y 从 +27~40bp 塌到 +0.7bp
```
方向阈值放宽(0.25→0.10)后量回来了(91 fills/h),但挂单离场的平均收益塌到 ~0:
弱信号的进场 ≈ 无捕获 + 无 markout。
更重要的是:**设计里一直传 tp_bp=60,但离场阶梯从未实现止盈** ⇒
赢家没有"落袋"这一档,行程回吐给市场。
```

### 7.2 止盈层(h876)
```
阶梯新增 maker_take:浮盈 ≥ tp_bp ⇒ **挂单离场(0 费)锁住行程**
位置:taker_stop(亏损尾部)之后、maker_risk 之前;tp 未设时默认 1:1(止损同宽)
active_flow 同时修复:tp_bp 不再被 del,真实传入阶梯。
单测 13/13 ✓;上线:fills 60/h | error 0
```

### 7.3 离场阶梯现在完整了
```
no_book → taker_risk(危险形态) → taker_stop(灾难尾部)
       → **maker_take(止盈落袋)** → maker_risk(浮亏一半抢出)
       → maker_edge(优势消失) → maker_time(时限) → hold
```
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h876")
