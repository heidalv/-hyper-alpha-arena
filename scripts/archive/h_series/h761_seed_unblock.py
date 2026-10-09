# -*- coding: utf-8 -*-
"""[h761] 把 SEI/PUMP 从静态负种子名单移出,交给 **L4 微试跑**仲裁。

背景(逐级漏斗诊断的终点):
  · 种子榜 data/coin_leaderboards_v1.json 生成于 2026-09-30,**没有任何刷新脚本**
    (全库只有 h329_selector_v5.py 读它;note 里"滚动重算由事件研究批处理任务承接"
    是 TODO,任务不存在)⇒ 币种名单实际上被冻结在 3 天前;
  · negative_seed = [XMR,AAVE,WLFI,TAO,PUMP,HYPE,ZEC,SEI,AVAX] 把这些币**永久排除**
    (selector h329 第 479 行:`neg_seed and max_edge < NEG_SEED_BAR ⇒ continue`);
  · 但实测:SEI 全价差 **13.9bp**、占比 0.97;PUMP **8.7bp**、占比 0.97 —— 是两个
    价差最宽、最符合 L16 捕获档要求的币。
处置:从 negative_seed 移除 SEI/PUMP(其余保留)。它们入槽后走**已有的**
L4 微试跑(12h 预注册 + d1_verdict 按实现每腿净仲裁,不过即摘出)——
比"3 天前的静态差评"更符合"后验证据优先"。
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
P = Path(r"D:\001Alpha\Hyper-Alpha-Arena\data\coin_leaderboards_v1.json")
d = json.loads(P.read_text(encoding="utf-8"))
before = list(d.get("negative_seed") or [])
remove = {"SEI", "PUMP"}
after = [s for s in before if str(s).upper() not in remove]
d["negative_seed"] = after
d["note"] = (str(d.get("note") or "")
             + " | [h761 2026-10-03] SEI/PUMP 移出负种子:价差 13.9/8.7bp(最宽),"
               "改由 L4 微试跑按实现每腿净仲裁;其余负种子保留。静态榜需滚动重算(未实现)。")
P.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("negative_seed 前:", before)
print("negative_seed 后:", after)
print("移出:", sorted(set(map(str.upper, before)) & remove))
