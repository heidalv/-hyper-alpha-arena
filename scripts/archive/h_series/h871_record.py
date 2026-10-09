# -*- coding: utf-8 -*-
"""[h871] 记录:桥 07:58 —— 近 1h 首次干净转正。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\Aster专属交易规则集_20261005.md")
s = p.read_text(encoding="utf-8")
add = """

## 3. 桥 07:58:近 1 小时首次干净转正(h871)

```
近 1h(n=36):
  挂单占比 **75%**(止损率 25%,低于 40.3% 盈亏平衡线)
  挂单离场平均 **+40.00bp**
  **组合期望 +12.49bp/笔(合计 +450bp)** ✓✓
近 60 分钟净值:**+2.384U** ✓
近 15 分钟 taker_stop:2 条(稳定在低位;修复前 6~18 条)

S1 累计:n=142 挂单率 64% 挂单 y +21.01bp 期望 −3.97bp/笔(此前 −17.6)
车道:fills 71.6/h | active_flow_error=0 | 单侧挂单 73356、双边 0 ✓

⇒ 两个新杠杆(流反转即时离场 + 按币分档止损)生效:止损率从 43~50% 打到 ~25%。
⇒ 4h/12h 窗口仍压着改动前的旧账(−14/−8bp),属预期。
⇒ 若 1h 的模式延续,24h 窗口将逐小时翻正。
```
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h871")
