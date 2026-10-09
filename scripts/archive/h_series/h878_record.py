# -*- coding: utf-8 -*-
"""[h878] 记录:用户三条深改 —— 成交后方向重算 / 止损只留真宽 / 到点只挂单。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\Aster专属交易规则集_20261005.md")
s = p.read_text(encoding="utf-8")
add = """

## 9. 用户三条深改(全部上线,h878,13:3x)

```
① **成交后方向重算(每仓一次)**:
   方向分数是挂单前算的;买单被打到 = 刚有人在卖。成交后用同一套因子
   (微价、资金流、5 分钟方向、15 分钟过冲)重算:
   分数翻到持仓对面 ⇒ flow_mu=-1 ⇒ 下一拍 maker_edge 在对手价挂平(0 费)。
   实现:runner 在 active_flow 之后做一次性重算(flow_rechecked 字段,每仓一次)。

② **止损只留给"真宽"**:
   吃单止损线从 stop 放宽到 **stop × 2**(STOP_WIDE_MULT=2);
   普通晃动全部交给 maker_risk(浮亏到止损一半 ⇒ 挂单抢平,0 费);
   吃单只发生在:浮亏真宽(深跳)或 R4/R5/盘口过期(没人成交)。
   依据:近 12h 181 笔吃单止损亏 ≈$158,挂单平掉的是赚的。

③ **到点只挂单**:
   持有时限到达 ⇒ maker_time 在卖一/买一挂平仓单,0 费;
   硬吃单时限常量已不再使用(到点吃单路径已不存在)。
```

### 上线实测
```
单测 13/13 ✓ | error 0 | fills 15/h
近 15 分钟 +0.46U | 近 30 分钟 +0.70U
(修过一处编辑事故:choose_exit 的 docstring/函数体重复段,已按行删除)
```
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h878")
