# -*- coding: utf-8 -*-
"""轮116：车道资金**重新分配**（.env 覆盖层）。

## 依据（用户 2026-09-18 确认的车道口径 = 轮63 `lane_semantics`）

    车道1 intraday 日内（含中线槽位） tier=mid
    车道2 trend    长线趋势           tier=long
    `scalp` 不再是独立车道：短线车道已停（2026-09-17），存量 scalp 归入 intraday

**但预算层还停在三车道时代**：`short` 那条车道已经永久关闭
（`SCALP_OPEN_DISABLED=true` / `lane_registry: "已证无边际，永久关闭"` /
30 天 853 笔净 −201.55、胜率 0.393、笔均 −0.122%），而它的配额
（tier 0.25 + layer 0.35）**从来没有被回收或重分配** —— 这正是
「短线没有[了]，自己分配比例、阀口都没动过」。

## 重新分配（总分配保持 0.85，安全边际 0.15 不变）

| 项 | 旧 | 新 | 理由 |
|---|---|---|---|
| `TIER_SHORT_BUDGET/MAX_MARGIN` | 0.25 | **0.00** | 车道已停（实测无边际） |
| `TIER_MID_BUDGET/MAX_MARGIN` | 0.25 | **0.35** | 日内车道：mlto 臂 30 天 38 笔 +41.79（笔均 +0.205%） |
| `TIER_LONG_BUDGET/MAX_MARGIN` | 0.35 | **0.50** | 趋势车道：trend_e1 臂 22 笔 +45.44（笔均 +1.128%，全车道最高） |
| `LAYER_BUDGET_SCALP` | 0.35 | **0.00** | 层随车道退役（scalp 池不再有钱） |
| `LAYER_BUDGET_TREND` | 0.65 | **0.85** | 必须 ≥ mid+long 的 tier 上限之和(0.85)，否则层上限成为实际约束、tier 配额形同虚设 |

释放的 0.25 按两个车道"最好的臂"的净额占比分：trend_e1 +45.44 : mlto-mid +41.79
= 52% : 48% ⇒ 趋势 +0.15 / 日内 +0.10（略偏向笔均高 5.5× 的趋势车道）。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")

PLAN = {
    "TIER_SHORT_BUDGET": "0.0",
    "TIER_SHORT_MAX_MARGIN": "0.0",
    "TIER_MID_BUDGET": "0.35",
    "TIER_MID_MAX_MARGIN": "0.35",
    "TIER_LONG_BUDGET": "0.50",
    "TIER_LONG_MAX_MARGIN": "0.50",
    "LAYER_BUDGET_SCALP": "0.0",
    "LAYER_BUDGET_TREND": "0.85",
}

HEADER = (
    "# [轮116 2026-09-19] 车道资金重新分配：预算层对齐轮63 的两车道口径\n"
    "#   短线车道已停（SCALP_OPEN_DISABLED=true / lane_registry 永久关闭 / 30 天 853 笔净 -201.55）\n"
    "#   ⇒ 配额 0.25(tier)+0.35(layer) 全部回收，释放给两个在跑的车道：\n"
    "#     日内(mid) 0.25 → 0.35   （mlto 臂 30 天 38 笔 +41.79）\n"
    "#     趋势(long) 0.35 → 0.50  （trend_e1 臂 22 笔 +45.44，笔均 +1.128%）\n"
    "#   总分配仍为 0.85（安全边际 0.15 不变）；trend 层 0.85 ≥ 0.35+0.50，\n"
    "#   否则层上限会盖住 tier 配额、分车道阀口形同虚设。\n"
    "# 回滚：把下面 8 行删掉即回到代码默认（short 0.15 / mid 0.35 / long 0.40，layer 0.40/0.60）。\n"
)


def main(apply: bool) -> int:
    raw = io.open(ENV, encoding="utf-8", errors="surrogateescape", newline="").read()
    lines = raw.split("\n")
    changed, missing = [], []
    for k, v in PLAN.items():
        hit = None
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith(k + "=") and not s.startswith("#"):
                hit = i
                break
        if hit is None:
            missing.append(k)
            continue
        old = lines[hit].strip()
        if old == f"{k}={v}":
            continue
        lines[hit] = f"{k}={v}"
        changed.append(f"{old}  →  {k}={v}")

    if missing:
        print("[!] 这些键在 .env 里不存在，需手工添加:", missing)
        return 2
    if not changed:
        print("已是目标值，无需改动")
        return 0

    print("将改动:")
    for c in changed:
        print("   ", c)
    if not apply:
        print("(干跑；加 --apply 落盘)")
        return 0

    txt = "\n".join(lines)
    if "轮116 2026-09-19] 车道资金重新分配" not in txt:
        txt = HEADER + txt
    io.open(ENV, "w", encoding="utf-8", errors="surrogateescape", newline="").write(txt)
    print("[OK] 已写入", ENV)
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
