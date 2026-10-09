# -*- coding: utf-8 -*-
"""[h865] 记录:形态→策略路由接上(用户"接上")。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\高频交易当前机制全图与设计差距_20261005.md")
s = p.read_text(encoding="utf-8")
add = """

## 4. G8 的决定性发现:之前设计的"趋势 + 多策略配置"全是死配置(h865,05:0x)

**用户:"之前设计的,趋势和不同策略配置呢?" —— 求证结果:**

| 之前的设计 | 现在状态 | 证据 |
|---|---|---|
| 趋势闸(h697 `trend_block_mode=3`、300s/60s 趋势、trend_flip_flatten) | ❌ **不执行** | 参数在、代码在(runner.py:2448),但主动流分支在 **1174 行 `return`** ⇒ 全绕过 |
| 形态矩阵(P1 回调族 / P45 动量族 + `pattern_matrix` 选币即配闸) | ❌ **既没配也不跑** | 车道 meta 的 `pattern_matrix` = **空**;形态检测在 1597 行(早返回之后) |
| 流闸族(h658 造血引擎:pullback/p5/p4) | ❌ **死配置** | 三个参数都是 0.3,同样在早返回之后 |
| 形态→策略映射(R1→S1 / R2→S3 / R3→S4 / R4R5→避险) | ❌ **零引用** | `regime.py` 的 `REGIME_STRATEGY` 没有任何地方 import |
| 反转学习器(h300)/EWA 进化(h294)/赌臂选币(h716) | ⚠️ **还在跑,喂给死端** | 任务在产文件,消费端是 MM 时代选择器 ⇒ 对主动流无影响 |

**根因:换引擎时把整条传动轴切断,但齿轮还留在车上转。**

## 5. 路由已接上(h865 实施)

```
① **按形态路由**(active_flow 内):
     R1 趋势流 → S1 流顺势  :OFI 与 300s 趋势同向 ⇒ 顺流挂单进场
     R2 平静   → S3 均值反转:偏离 60s VWAP ≥ 12bp 且**无强流** ⇒ 逆偏离进
     R3 挤压   → S4 挤压突破:σ 抬升 + 流明确 ⇒ 顺突破进
     R4/R5     → S5 避险    :不进场
② **逐策略持有时限**:S1 45s / S3 120s / S4 90s(edge 生命周期不同)
③ **逐策略记账**:往返日志新增 `strategy` 与 `hold_sec` 字段
   ⇒ 各策略可独立学习与裁决(以前只有一套口径)
④ 与并行实现对齐:策略 id 优先取门 reason 的前缀(explore_s1/s3/s4),
   没有时回落到按形态自选 —— 两处实现不打架
```

**修复的一个自伤 bug**:函数开头 `del ofi, trend_bp, flow_thresh` 把路由要用的名字删了
⇒ 路由一访问就 UnboundLocalError(实测 `active_flow_error` 62 次)。已改为只 del 真正不用的。

### 实测(路由上线后 10 分钟)
```
往返按策略:{'S1': 8 条, '(空)': 4 条(改动前的)}
**挂单率 75%(止损率 25%)** ← 低于 40.3% 的盈亏平衡线 ✓
active_flow_error:0 ✓ | fills 169/h | 单测 13/13 通过
```
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h865")
