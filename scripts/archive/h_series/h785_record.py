# -*- coding: utf-8 -*-
"""[h785] 执行记录:双 worker 事故 + BTW 跳空 + 波动定档缩量。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\方向判定改造执行记录_20260930.md")
s = p.read_text(encoding="utf-8")
add = """

### 六bl、双 worker 事故修复 + BTW −920bp 跳空 + 波动定档缩量(h785,10-04 03:2x)

用户:"依然崩,并且崩的严重了"。查实有两个叠加原因:

**① 双 worker 实例(我的重启引入的放大事故)**
- 03:01 与 03:23 各有一对 worker 进程同时在跑 ⇒ 双重仓位/重复下单;
- 机理:部署 h784 时 Stop-Process 杀 worker ⇒ 看门狗 `mm-worker-guard.vbs`
  的循环**秒级重生** ⇒ 我每杀一次它就再拉一个,形成杀-生循环(实测杀完
  1 秒内新 pair 出现)。
- 修复:先杀 guard(wscript 16596 + powershell 30176)再杀 worker、清锁,
  以 start-mm-worker-hidden.vbs 起**单个** worker(03:24,pid 12784/11672)。
  验证:仅一对进程、挂单 97%。

**② BTW 03:13:57 一条 −919.8bp 止损腿 = −$3.00U**
- 名义 $33(硬上限已把旧 $441 仓位压到 $33 级),−9.2% 跳空;
- 旧结构下同样的跳空是 $441×9.2% = −$40 级 ⇒ 硬上限已把伤害降了一个数量级;
- 跳空在 15s 桶内完成,速退/止损都在跳空价成交,任何出场机制都追不上,
  **唯一能兜住的是仓位大小**。

**处置(h785)**:`size_vol_decay 1.0 → 2.0` —— 高σ时段加仓腿自动缩小
(h621 证据:满额腿 −1.34bp vs 被削腿 +0.79bp/腿),跳空来的时候只伤小仓位。
30 分钟快判(metric=fills_per_hour:每腿净没恶化且成交没大降 ⇒ 保持)。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h785")
