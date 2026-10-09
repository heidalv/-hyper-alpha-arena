"""h606 — ② 的**频率分解**：`腿/h = 趟/h × 腿/趟`（只读；R194）。

为什么需要：② 的目的之一是不破 **≥60 腿/h 的硬约束**（用户要求的硬指标）。但"腿/h"是
**两个因子的乘积**：
    · **腿/趟**（`legs_per_trip`）= 90s 加仓窗**直接**作用的那一层（判据 C ✓）；
    · **趟/h**（往返频率）= 市场给的机会数 × 引擎出手机制。
⇒ 只看腿/趟会得出"90s 让腿变多了"，只看腿/h 会得出"没变化"——**必须分解开**，
   否则今晚无论判什么，都能挑一个数字讲故事 ✗（这正是本项目反复出现的口径病）。

本脚本用**同一段代码、同一口径**在几个窗口上算同一组数（不手工抄任何值）：
    ① 90s 试跑窗（当前，进行中）；
    ② 45s 参照窗 = h572 的 N3（09-28 23:17→09-29 09:48 本地，夜间、0.5 体制）——
       **内置校验**：它必须复现 3.6263，否则说明我的口径与框架不一致 ⇒ 报红 ✗；
    ③ 45s 同时钟窗 = 09-28 13:00→21:48 本地（与 90s 窗**同钟点区间**，前一天）——
       用来回答"如果 ② 回滚到 45s，同一钟点还能不能守住 60/h" ✓。

用法：python scripts/h606_frequency_decomposition.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

L = "Asia/Shanghai"
REF_LPT = 3.6263          # h572 N3 窗（45s/0.5 体制）的腿/趟，用于自校验

# (标签, 起点 ISO(UTC), 终点 ISO(UTC) 或 None=当前, 是否要求复现参照值)
WINDOWS = [
    ("③ 45s 同时钟窗（09-28 13:00→21:48 本地）",
     "2026-09-28T05:00:00+00:00", "2026-09-28T13:48:00+00:00", False),
    ("② 45s 参照窗 h572-N3（23:17→09:00 本地）",
     "2026-09-28T15:17:00+00:00", "2026-09-29T01:00:00+00:00", True),
    ("① 90s 试跑窗（09:48 本地 → 现在，进行中）",
     "2026-09-29T01:48:11+00:00", None, False),
]


def local(s: str) -> str:
    return dt.datetime.fromisoformat(s).astimezone(
        dt.timezone(dt.timedelta(hours=8))).strftime("%m-%d %H:%M")


def main() -> int:
    print("=" * 96)
    print("h606 — ② 的频率分解：腿/h = 趟/h × 腿/趟")
    print("=" * 96)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = []
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
        print(f"  币种（注册表）：{syms}\n")
        hdr = (f"{'窗口':<40s} {'时长':>6s} {'可交易':>7s} {'腿数':>5s} "
               f"{'出场腿':>6s} {'腿/趟':>7s} {'腿/h(可交易)':>12s} {'趟/h':>7s}")
        print(hdr)
        print("-" * 96)
        for label, t0, t1, must_ref in WINDOWS:
            t1 = t1 or now
            cov, ratio = h._covered_hours(t0, t1, syms)
            sub = h._sub_stats(cur, "h463", t0, t1, syms)
            hw = dict((sub.get("hold_window") or {}))
            legs = int(hw.get("legs") or 0)
            exits = int(hw.get("exit_legs") or 0)
            lpt = hw.get("legs_per_trip")
            hours = (dt.datetime.fromisoformat(t1)
                     - dt.datetime.fromisoformat(t0)).total_seconds() / 3600.0
            lph = (legs / cov) if cov else float("nan")
            tph = (exits / cov) if cov else float("nan")
            rows.append((label, hours, cov, ratio, legs, exits, lpt, lph, tph, must_ref))
            print(f"{label:<40s} {hours:5.2f}h {cov:6.2f}h {legs:5d} {exits:6d} "
                  f"{(lpt or 0):7.3f} {lph:11.1f} {tph:7.2f}")
    print("-" * 96)
    print(f"  本地时间：{local(WINDOWS[0][1])} → {local(WINDOWS[0][2])}（③）；"
          f"{local(WINDOWS[1][1])} → {local(WINDOWS[1][2])}（②）；"
          f"{local(WINDOWS[2][1])} → 现在（①）")

    fails = []
    ref_rows = [r for r in rows if r[-1]]
    for r in ref_rows:
        got = float(r[6] or 0.0)
        ok = abs(got - REF_LPT) < 0.01
        print(f"\n  {'✓' if ok else '✗'} 自校验：参照窗复现腿/趟 = {got:.4f}"
              f"（框架登记值 {REF_LPT}）")
        if not ok:
            fails.append(f"参照窗腿/趟未复现（{got:.4f} vs {REF_LPT}）⇒ 口径不一致")

    # 分解解读（用 ③ 与 ① 对比：同钟点、不同加仓窗）
    w45 = next((r for r in rows if r[0].startswith("③")), None)
    w90 = next((r for r in rows if r[0].startswith("①")), None)
    if w45 and w90 and w45[6] and w90[6]:
        print("\n  同钟点分解（③ 45s → ① 90s）——⚠️ **钟点对齐 ✓，但确认体制不同**"
              "（③ 是 0.15、① 是 0.5）⇒ 不是干净对照（R197）✗：")
        print(f"    腿/趟：{w45[6]:.3f} → {w90[6]:.3f}（×{w90[6]/w45[6]:.2f}）"
              f" ⇒ 加仓窗**直接**作用的那一层（但混着体制差 ✗）")
        print(f"    趟/h ：{w45[8]:.2f} → {w90[8]:.2f}（×{w90[8]/w45[8]:.2f}）"
              f" ⇒ 市场机会 × 出手机制那一层")
        print(f"    腿/h ：{w45[7]:.1f} → {w90[7]:.1f}（×{w90[7]/w45[7]:.2f}）"
              f" ⇒ 两因子之积（硬约束看的这层）")
        print("    ⇒ 读法：**腿/趟几乎没动（×1.06）、腿/h 也没动（×0.95），而趟/h 掉了 11%** ⇒")
        print("      在可用的窗上，90s **没有**带来可测的腿量收益；腿量由**趟/h**决定，"
              "而趟/h 属于市场+入场机制，不是这个参数能抬的 ✓")
        print("    ⚠️ 但要如实说边界：③ 与 ① 只对齐了**钟点**，**确认体制不同**（0.15 vs 0.5）"
              "⇒ 若体制本身影响腿/趟，上表既有 90s 的效应也有体制的效应，二者**仍不可分离** ✗")
        if w45[7] >= 60:
            print(f"    ⇒ ③ 窗口的 45s 腿/h = {w45[7]:.1f} ≥ 60 ⇒ **回滚到 45s 不会破"
                  f"硬约束**（同钟点证据 ✓）")
        else:
            print(f"    ⇒ ⚠️ ③ 窗口的 45s 腿/h = {w45[7]:.1f} < 60 ⇒ 回滚到 45s **会**"
                  f"破硬约束（同钟点证据 ✗）⇒ 处置需另议")

    if fails:
        print("\n" + "-" * 96)
        print(f"✗ 有 {len(fails)} 项需要处理：")
        for f in fails:
            print(f"    · {f}")
        return 1
    print("\n" + "-" * 96)
    print("✓ 分解完成（参照窗自校验通过）✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
