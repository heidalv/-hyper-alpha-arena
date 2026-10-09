# -*- coding: utf-8 -*-
"""[h856] 记录:NoneType 崩溃修复 + 六层修复全部在线。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\流交易策略体系总体设计_20261004.md")
s = p.read_text(encoding="utf-8")
add = """

### 18.5 修复过程中的第七处(自己引入的崩溃,已修)
```
现象:skip 里出现 active_flow_error 9331 次(半个车道空转),日志:
      "float() argument must be a string or a real number, not 'NoneType'"
根因:`active_flow.py` 里 `state.flow_mu = float(model_mu)` ——
      我让探索门传 mu=None(有意为之:没有模型证据),这行没做 None 保护。
修法:`float(model_mu or 0.0)`(存 0.0 = "剩余期望未知/不为正"
      ⇒ 离场在最短持有后走 maker_edge 挂单)。
教训:与 L20(静默死)同族 —— **改参数语义必须同时扫一遍所有消费点**。
```

### 18.6 六层修复全部在线后的实测(01:3x)
```
skip 分布:maker_working 10900 | explore_trend_veto 6435 |
          maker_edge 344 ✓ | **maker_risk 171 ✓(新的挂单止损层在动)** |
          holding 28 | **active_flow_error 0 ✓**
挂单:one=14595 / both=0(全单侧)✓ | fills/h 53
近 8 分钟往返:挂单 1 条 **+47.3bp** | 吃单 2 条 −28.8bp
```
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h856")
