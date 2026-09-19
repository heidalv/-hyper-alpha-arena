# -*- coding: utf-8 -*-
"""轮121：按用户指令**去掉 E1 长车道独占**（TREND_E1_LONG_LANE_EXCLUSIVE）。

## 这条规则是什么

`paper_trading_engine.place_order` 收口处调用
`trend_e1_engine.long_lane_open_allowed()`：当独占开关为 true 时，
**非 E1 来源的 `tier=long` 新开仓一律拒绝**（只记为提议）。
来源：`.env` 注释「[v3 方向1 2026-09-03 p1-trend-engine] 启用即独占长车道：
LLM 中长线流的 tier=long 新开仓只记为提议（被拒）」——
**用户 2026-09-19 明确表示：从没这么设计过、不知道这条规则、也没被报审批。**

## 去掉它意味着什么（如实写清）

* 恢复：LLM/中长线流的 `tier=long` 新开仓可以真正下单（不再只记提议）；
* **不影响**平仓/减仓（该闸本来就对 reduce/close 放行）；
* **不影响**中线/短线（该闸只判 `lane == "long"`）；
* 与 E1 仓位的关系：E1 的**退出**保护（轮96 的 A/B/C/D 四修：中线退出逻辑不得
  收割 E1 长线仓）**不在本开关里**，本轮不动。

## 回滚

把 `.env` 的 `TREND_E1_LONG_LANE_EXCLUSIVE` 改回 `true` 即恢复独占。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
KEY = "TREND_E1_LONG_LANE_EXCLUSIVE"
HEADER = (
    "# [轮121 2026-09-19] 按用户指令去掉 E1 长车道独占。\n"
    "#   来源：2026-09-03 「v3 方向1」自己加的规则 —— 用户明确表示：从没这么设计过、\n"
    "#   不知道有这条、也没被报审批。它的作用是「非 E1 来源的 tier=long 新开仓只记为提议」。\n"
    "#   注意：它**只拦 long 车道**（中线/短线不受影响），也不拦平仓/减仓。\n"
    "#   回滚：改回 true 即恢复独占。\n"
)


def main(apply: bool) -> int:
    raw = io.open(ENV, encoding="utf-8", errors="surrogateescape", newline="").read()
    lines = raw.split("\n")
    hit = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith(KEY + "=") and not s.startswith("#"):
            hit = i
            break
    if hit is None:
        print(f"[!] .env 里没有 {KEY}= 行")
        return 2
    old = lines[hit].strip()
    print("将改动:", old, "→", f"{KEY}=false")
    if not apply:
        print("(干跑；加 --apply 落盘)")
        return 0
    lines[hit] = f"{KEY}=false"
    txt = "\n".join(lines)
    if "轮121 2026-09-19] 按用户指令去掉 E1 长车道独占" not in txt:
        txt = HEADER + txt
    io.open(ENV, "w", encoding="utf-8", errors="surrogateescape", newline="").write(txt)
    print("[OK] 已写入", ENV)
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
