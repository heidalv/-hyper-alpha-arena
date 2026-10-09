"""修正 H31/H32/H34 的 `abs(mk)` 公式错误。

## 错在哪

三处都写了 `net = half - abs(mk)`。但 `mk`（markout）**已经自带方向**：
    · 买单：`mk = (mid_{T+τ}/px − 1)·1e4`，mid 涨 ⇒ mk>0 ⇒ 我们**赚**
    · 卖单：`mk = (1 − mid_{T+τ}/px)·1e4`，mid 跌 ⇒ mk>0 ⇒ 我们**赚**
所以净额应为 `net = half + mk`（符号自带方向）。

用 `abs(mk)` 会**把赚的那一半也当成亏**，等价于把 markout 的
**标准差**当成**成本**：

    E|mk| ≈ 0.8·σ(mk)   ≫   E[mk]

实测（BTC，2h，135 笔，`_dbg_h34_decomposition.py`）：

    tau      half均值    mk均值    错误公式 half-|mk|   正确 half+mk   差
    1s       -0.0033    -0.0334    -0.4002            -0.0367       0.3635
    5s       -0.0033    -0.1308    -0.5784            -0.1341       0.4443
    30s      -0.0033    -0.0837    -1.5524            -0.0870       1.4654

**30s 上差 1.47bp** —— 这就是 H31/H32/H34 报出 −1.4~−1.5bp/笔的来源。

## 本脚本做什么

把三处的 `- abs(mk)` 改成 `+ mk`，并在旁边写清为什么。
（`half` 本身的符号也要核对：它是「成交时中价 − 挂单价」，对买单成交于 bid
 通常为正；若为负说明中价已跌穿我们的挂单价 —— 那是真实逆向选择，保留。）

用法：
    .venv\\Scripts\\python.exe scripts\\_fix_abs_markout.py [--apply]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

NOTE = ("  # [修正] `mk` 自带方向（买单 mid 涨为正=赚）⇒ 不能用 abs()。\n"
        "            `- abs(mk)` 会把 markout 的**标准差**当成**成本**：\n"
        "            E|mk| ≈ 0.8σ(mk) ≫ E[mk]，实测 30s 上虚增 1.47bp/笔。\n")

RULES = [
    # (文件, 旧串, 新串)
    ("h31_signal_timed_quoting.py",
     'st["net"].append(sp_half_bp - abs(mk))',
     'st["net"].append(sp_half_bp + mk)'),
    ("h31_signal_timed_quoting.py",
     'st["net"].append((ask - mid[i]) / mid[i] * 1e4 - abs(mk))',
     'st["net"].append((ask - mid[i]) / mid[i] * 1e4 + mk)'),
    ("h32_real_queue_depth_retest.py",
     'st["net"].append(sp_half_bid - abs(mk))',
     'st["net"].append(sp_half_bid + mk)'),
    ("h32_real_queue_depth_retest.py",
     'st["net"].append(sp_half_ask - abs(mk))',
     'st["net"].append(sp_half_ask + mk)'),
    ("h34_tick_level_timed_quoting.py",
     "net = hs - abs(mk)",
     "net = hs + mk"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    total = 0
    for fname, old, new in RULES:
        p = ROOT / "scripts" / fname
        if not p.exists():
            print(f"  跳过（不存在）: {fname}")
            continue
        txt = p.read_text(encoding="utf-8")
        n = txt.count(old)
        if n == 0:
            print(f"  未命中（可能已修）: {fname}  <<{old[:48]}>>")
            continue
        # 在替换后的行**上方**插入说明（只插一次）
        repl = new
        txt2 = txt.replace(old, repl)
        marker = f"# [F259 修正] {fname} 的 markout 公式（见 _fix_abs_markout.py）"
        if marker not in txt2:
            # 在最大 import 块之后插入一条模块级说明
            lines = txt2.splitlines()
            ins = 0
            for i, ln in enumerate(lines[:120]):
                if ln.startswith(("import ", "from ")) or ln.strip() == "":
                    ins = i + 1
            lines.insert(ins, "")
            lines.insert(ins + 1, marker)
            lines.insert(ins + 2, "# net = half + mk（mk 自带方向）")
            lines.insert(ins + 3, "# 旧写法 half - abs(mk) 把 markout 的标准差当成成本，")
            lines.insert(ins + 4, "# 实测 30s 上虚增 1.47bp/笔（BTC 2h 样本）。")
            txt2 = "\n".join(lines) + "\n"
        if apply:
            p.write_text(txt2, encoding="utf-8")
        print(f"  {'已修' if apply else '待修'}: {fname}  替换 {n} 处")
        total += n
    print(f"\n合计 {total} 处{'（已写入）' if apply else '（预览，加 --apply 写入）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
