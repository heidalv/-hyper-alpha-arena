# -*- coding: utf-8 -*-
"""[h797] 执行记录:宇宙收敛到 6 币 + 流边优先入选择器。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\方向判定改造执行记录_20260930.md")
s = p.read_text(encoding="utf-8")
add = """

### 六bt、选币机制执行:宇宙收敛 6 币 + 流边优先(h797,10-04 13:4x)

- `universe_slots 12 → 6`(集中样本,流边测得出);
- 选择器 `choose_slots` 修复"slots 只限制新增、在槽币永不裁剪"的老行为:
  **在槽币也按流边排序,超出 6 个的最弱者裁出**(否则 12 币宇宙永不收敛);
- 选择器排序键并入流边:`flow_tradeable +100 / 已测出不可交易 −50 / 样本不足 0`;
- 验证:提案 `['1000SHIB','NEAR','PENGU','CBRS','COIN','RESOLV']`(6 币),
  摘出 `['ENJ','EVAA','FIL','SKY','2Z','BCH']`;NEAR(流边 92.8bp)保留;
  US(流边 703bp)在 decayed_out 中(旧账亏损淘汰),冷却后回归并被 +100 优先。
- 雷达下一周期(≤5 分钟)自动执行换币。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h797")
