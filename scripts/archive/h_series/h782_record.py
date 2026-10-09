# -*- coding: utf-8 -*-
"""[h782] 执行记录:裁决引擎补上 exit_cost 判据(修正 23:58 误回滚)。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\方向判定改造执行记录_20260930.md")
s = p.read_text(encoding="utf-8")
add = """

### 六bj、裁决引擎补上出场成本判据(h782,10-04 00:0x)

**23:58 事故**:我 23:24 给 reduce_touch 登记的 verdict_metric=exit_cost,
但裁决引擎 `self_tuner_verdict.py` 的 `metric_value()` **不认这个指标** ⇒ 落入
默认的"每腿净"分支 ⇒ 30 分钟窗口为负 ⇒ 又把贴盘口回滚了(第二次)。

**而正确的数据(23:58 的 exit_cost KPI)显示它其实在生效**:
  超时腿吃价差 −8.64bp → **−4.46bp(改善 48%)**;净 −11.84 → −5.68bp。
⇒ 两次回滚 reduce_touch(22:43/23:58)都是**判据错误**,不是参数无效。

**修复**:
- `metric_value()` 新增 `exit_cost` 分支:读 data/exit_cost_last.json,
  返回 (timeout_n, timeout_cross_bp);
- main 里新增 exit_cost 裁决分支(按参数定规则):
  · reduce_touch_after_sec:吃价差向 0 改善 ≥1bp **或** timeout_n 降 ≥30%(对基线)
    ⇒ keep;否则 rollback;
  · jump_exit_bp:jump_n(假摔率)≥15/2h ⇒ rollback;
  · max_net_directional_ratio:不回滚(焊死的代码硬上限兜底);
- reduce_touch 300 重新启用,登记 baseline_cross_bp=−4.455 / baseline_n=17。

**桥 23:58 其余**:E KPI **0.0 健康**(markout +0.09 vs 捕获 +4.20,全天最好);
G 成交审计 100% 合法、每腿 +2.43bp(n=737);F:1h −1.13bp/腿 −2.72U,
4h −0.64bp,24h −27.9U(仍背着 LYN/SI 两条巨腿 + 22:43 误回滚后的敞口期)。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h782")
