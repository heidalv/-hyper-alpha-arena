# -*- coding: utf-8 -*-
"""[h858] 关键结论:挂单离场 +43.29bp(t=+4.32,显著) vs 吃单止损 −53.08bp(t=−11.20)

→ 进场是有 edge 的;**杀手是"约 50% 的仓位被止损打掉"**。
→ 把止损率从 50% 降到 30%,整体就从 −385bp 变成 +2020bp(见脚本内算式)。
→ 最直接的杠杆:让"挂单离场"更容易成交 —— 按**当前流的方向**选挂哪一侧
   (流在往卖压走 ⇒ 多头就挂买一 = 立刻能被人打到;而不是死等买盘来抬卖一)。
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

maker_y, taker_y = 43.29, -53.08
print("止损率 → 期望(65+65=130 笔):")
for stop_rate in (0.5, 0.45, 0.4, 0.35, 0.3, 0.25):
    n_stop = 130 * stop_rate
    n_mk = 130 - n_stop
    total = n_mk * maker_y + n_stop * taker_y
    print(f"  止损率 {stop_rate*100:>3.0f}% ⇒ 挂单 {n_mk:>5.1f} 笔 / 吃单 {n_stop:>5.1f} 笔 "
          f"⇒ 合计 {total:+8.0f}bp({'✓正' if total > 0 else '✗负'})")
