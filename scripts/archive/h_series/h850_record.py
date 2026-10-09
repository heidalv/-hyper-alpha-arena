# -*- coding: utf-8 -*-
"""[h850] 记录:两个面板加限高滚屏(用户报"页面被拉长")。"""
from pathlib import Path

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\研究结论\流交易策略体系总体设计_20261004.md")
s = p.read_text(encoding="utf-8")
add = """

## 17. 前端修复:两个面板没有滚屏 ⇒ 页面被拉长(10-05 00:4x)

用户截图两个面板,币数一多就把页面撑长:

| 面板 | 文件 | 修法 |
|---|---|---|
| 实时分析预测(微价·流·趋势融合) | `HftControlPanel.tsx:756` | 加 `max-h-64 overflow-y-auto` ⇒ 面板内滚动 |
| 分币种实盘判定(#2 试跑口径) | `app/hft/page.tsx:330` | 加 `max-h-[420px] overflow-y-auto` + 表头 `sticky top-0` ⇒ 表体内部滚动、表头常驻 |

根因:宇宙已从 6 币涨到 30+ 币(探索模式 + 成交活跃选币),两张表都是
"行数 = 币数"且此前**没有高度上限** ⇒ 左列/整页被拉长。
验证:`/hft` 返回 HTTP 200(17.5KB),后端 `/api/health` 200 ✓;
前端是 Next dev(端口 5273,热更新)⇒ 刷新即生效。
"""
p.write_text(s.rstrip() + add, encoding="utf-8")
print("OK recorded h850")
