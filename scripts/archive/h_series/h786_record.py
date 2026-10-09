# -*- coding: utf-8 -*-
"""[h786] 执行记录:回填崩盘熔断 + worker 空转事故收尾。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\方向判定改造执行记录_20260930.md")
s = p.read_text(encoding="utf-8")
add = """

### 六bm、当场修复:回填崩盘熔断 + worker 空转收尾(h786,10-04 03:3x)

用户"为什么还要等下一桥,现在就是在修复" ⇒ 当场验证发现两个漏洞并修:

**漏洞 1:崩盘熔断在重启后失忆 + SI 没进 4h 淘汰**
- 验证实测:pnl_decayed_coins = [2Z, BTW, MARSCOIN] —— **SI 不在**(它入宇宙
  才 ~2h,腿数 < 4h/30 腿门槛),而 h784 的内存态在 worker 重启时被清空,
  重启后没有新深亏损腿就不会再触发 ⇒ SI 继续被加仓。
- 修复(h786):`_refresh_decay_block` 每 60s **回填扫描**近 60 分钟的深亏损
  平仓腿(price_bp < −150)⇒ 立即拉黑 30 分钟(幂等)。
- 实测生效(worker.err.log):[h786] 回填崩盘熔断:BTW / SI;
  [h759] 实时淘汰集合变更:→ ['2Z','BTW','MARSCOIN','SI'] ✓。

**事故 2:worker 空转 7 分钟(我的排查失误)**
- 03:27:34 worker 因"锁被夺"退出;我随后禁用了两个看门狗任务;
- 之后我反复"看到"新 worker pair —— **其实是我自己的探测命令的进程**
  (powershell/harness 的命令行里含 'mm_lane_worker.py' 搜索词)被自己匹配;
- 真实状态:python 名下的 worker 进程数 = **0**,车道空转 7 分钟。
- 收尾:以"基础解释器 + PYTHONPATH + -WindowStyle Hidden + 重定向"方式
  起单个 worker(03:38,pid 24128,ticks 正常推进),**重新启用**两个看门狗任务
  (锁已干净,不再产生双实例)。教训:探测进程时用 `Name='python.exe'` 过滤,
  且**禁用看门狗后必须记得恢复**。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h786")
