# -*- coding: utf-8 -*-
"""轮117：按**正确时代**重配两车道比例（撤回轮116 基于 30 天混账的偏斜）。

## 为什么撤回轮116 的比例
轮116 用 30 天样本把比例定成 mid 0.35 / long 0.50，理由是"trend_e1 +45.44 vs
mlto-mid +41.79"。但那 30 天里跨了 **5 个配置时代**（09-18 23:36 轮96 → 09-19 15:48 轮116
共 40 个 commit），样本被已停开的车道与已删除的路径污染：

    30 天（错）                 n=1047  净 **−80.80**  笔均 −0.107%  胜率 0.411
    09-18 之后（车道分离起）      n=  48  净 **+20.69**  笔均 +0.435%  胜率 0.625
    09-19 00:00 之后             n=   7  净 **+11.26**  笔均 +0.969%  胜率 0.714

⇒ 用户指出的方法错误成立：**用混了时代的账配今天的钱**。
正确时代下两个车道都是正的，且样本（48 笔）**不足以支撑任何精调偏斜**。

## 本轮取值
    short 0.00（结构事实：车道永久关闭，与业绩无关 —— 不变）
    mid   0.40  ← 日内车道（近期成交最密：09-19 4 笔 +1.901%/笔、胜率 1.000）
    long  0.45  ← 趋势车道（trend_e1 笔均最高，但两天只有 4 笔）
    layer scalp 0.00 / trend 0.85（总量 0.85 不变，安全边际 0.15 不变）
刻意**不做偏斜**：2 天 48 笔的样本量不支持；等现役配置时代累计 ≥100 笔平仓再重分。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")

PLAN = {
    "TIER_MID_BUDGET": "0.40",
    "TIER_MID_MAX_MARGIN": "0.40",
    "TIER_LONG_BUDGET": "0.45",
    "TIER_LONG_MAX_MARGIN": "0.45",
}

OLD_HEADER = "# [轮116 2026-09-19] 车道资金重新分配：预算层对齐轮63 的两车道口径"
NEW_HEADER = (
    "# [轮116→117 2026-09-19] 车道资金重新分配（**按时代修正后的比例**）\n"
    "#   结构事实：短线车道永久关闭 ⇒ 配额归零（与业绩无关）\n"
    "#   比例修正：轮116 的 mid 0.35 / long 0.50 是用 **30 天混账**定的（跨 5 个配置时代，\n"
    "#     样本被已停车道/已删路径污染：30 天净 −80.80 vs 09-18 之后净 **+20.69**）⇒ 撤回。\n"
    "#     现役时代两车道都为正，但只有 48 笔 ⇒ 不做偏斜：mid 0.40 / long 0.45。\n"
    "#   总量仍 0.85（安全边际 0.15 不变）；trend 层 0.85 ≥ 0.40+0.45。\n"
)


def main(apply: bool) -> int:
    raw = io.open(ENV, encoding="utf-8", errors="surrogateescape", newline="").read()
    lines = raw.split("\n")
    changed = []
    for k, v in PLAN.items():
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith(k + "=") and not s.startswith("#"):
                if s != f"{k}={v}":
                    changed.append(f"{s}  →  {k}={v}")
                    lines[i] = f"{k}={v}"
                break
    if not changed:
        print("已是目标值")
        return 0
    print("将改动:")
    for c in changed:
        print("   ", c)
    if not apply:
        print("(干跑；加 --apply 落盘)")
        return 0
    txt = "\n".join(lines)
    if OLD_HEADER in txt:
        txt = txt.replace(OLD_HEADER, NEW_HEADER, 1)
    io.open(ENV, "w", encoding="utf-8", errors="surrogateescape", newline="").write(txt)
    print("[OK] 已写入", ENV)
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
