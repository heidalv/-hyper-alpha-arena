# -*- coding: utf-8 -*-
"""[h874] 记录:跳空过滤分级(硬名单不碰 + 软名单小名义)+ 桥 10:58。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\Aster专属交易规则集_20261005.md")
s = p.read_text(encoding="utf-8")
add = """

## 5. 桥 10:58 + 跳空分级(h874)

### 5.1 最干净的一小时
```
近 1h:4 条往返 挂单率 **100%** 挂单 y +8.80bp 组合期望 **+8.80bp/笔** ✓
近 60 分钟 +0.86U | 近 30 分钟 +0.94U | 深跳止损(−60bp 以下):**0 条** ✓
```

### 5.2 但代价:fills 掉到 8/h
```
硬排除(gap_excluded + gap_prone 全并)把宇宙砍掉一大块 ⇒ 交易几乎停摆
—— 与用户"满速跑、数据从交易攒"的要求冲突。
```

### 5.3 分级修正
```
硬名单(gap_excluded):完全不碰
**软名单(gap_prone)  :按探索单小名义做(equity×5%)** —— 深跳成本压到 1/5,
                       同时这些币的样本不断。
上线:gap_soft_probe 415 次 ✓ | fills 恢复到 15/h | error 0
近 30 分钟 +0.94U、近 60 分钟 +0.78U(维持正)
```
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h874")
