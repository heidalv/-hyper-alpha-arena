# -*- coding: utf-8 -*-
"""[h875] 记录:方向阈值放宽(0.25→0.10),交易量恢复。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\Aster专属交易规则集_20261005.md")
s = p.read_text(encoding="utf-8")
add = """

## 6. 桥 11:58 + 交易量恢复(h875)

### 现象
```
过滤叠加后 fills 掉到 12/h —— 最大的拦路虎是 no_direction(direction_score 阈值 0.25 太严)
1h 窗口只剩 6 条往返,无法判断,也违背"满速跑、数据从交易攒"。
```

### 动作
```
MM_FLOW_DIR_MIN 0.25 → **0.10**(环境变量,无需改码)
上线:no_direction 41589 → 2640/窗口 ✓;fills **12 → 91/h** ✓;error 0
```

### 当前(12:1x)
```
近 15 分钟 12 腿 −1.26U(2 条止损 −0.98U,量刚恢复,窗口太短)
所有风险过滤仍在:gap 硬/软分级、R4/R5、负桶否决、流反转离场、趋势否决、毒性成交
```
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h875")
