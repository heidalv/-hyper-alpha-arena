# -*- coding: utf-8 -*-
"""[h873] 记录:深跳止损的头号来源 LYN + gap_prone 过滤补漏。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\Aster专属交易规则集_20261005.md")
s = p.read_text(encoding="utf-8")
add = """

## 4. 深跳止损的头号来源 + 补漏(h873,10-05 10:0x)

### 4.1 现象:挂单率 70% 但期望仍 −23bp/笔?
```
近 1h 挂单率 70%、挂单 y +27bp,但组合期望 −23.44bp/笔
⇒ 反推:剩余 30% 的止损每笔平均 **−141bp** —— 不是止损率高,是剩下的止损"深跳"。
```

### 4.2 定位(近 3h 止损按币)
```
LYN n=42 平均 **−86.5bp** 最差 **−536.6bp** 合计 **−29.48U** ← 头号
其余所有币合计约 −2.6U
根因:LYN 24h 振幅 **3528%**、gap_prone=**True**,却不在 gap_excluded 名单里
     (名单由第一级筛选器的另一套逻辑产生,漏了它)⇒ 车道照做,42 次被跳空打穿。
```

### 4.3 修法
```
跳空过滤不再只信 gap_excluded 名单,**并上 detail 里的 gap_prone 标记**
⇒ LYN 类币直接出局(连探索都不做)。
上线:gap_excluded 拦截从 ~2000 升到 3279 次/窗口 ✓;fills 87→17/h(危险币全退)
近 30 分钟 +0.65U、近 10 分钟 +0.13U(剩余交易都在正常币上)
```
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h873")
