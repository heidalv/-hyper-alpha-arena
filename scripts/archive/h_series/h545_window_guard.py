"""h545：**窗口守卫**——停掉"窗口已被停机污染、判决本就作废"的判定任务，
以保护 ②③ 的单变量窗口。

为什么必须做（R31 实证，不是推理）：
  · 判定框架的自动断点检测只统计含 `deploy/rollback/restore` 的 ops 动作
    （`h425_repair_trial.py:831`）⇒ **一次真的回滚会写进 ② 的窗口**，把它变成
    INCONCLUSIVE；
  · 干跑五条即将在 ② 窗口（09:26→21:24）内触发的判定：
      h443 INCONCLUSIVE / h452 INCONCLUSIVE / h454 INCONCLUSIVE / h435 INCONCLUSIVE
      **h472 → ROLLBACK（`engine_stall 21.6/h < 24/h`）** ✗✗
    ⇒ h472 会在 14:33 把 `ofi_flatten_maker_only` 1.0→0.0（废掉 +0.32$/h 的手续费修复）
      并在 ② 的窗内留下 rollback 记录。
  · 这些试跑的窗口**都跨了 04:32–09:09 的停机** ⇒ 判决无论如何都作废
    ⇒ 停掉它们**不损失任何保护**，只是把窗口卫生恢复。

用法：
  python scripts/h545_window_guard.py                # 干跑：列出计划
  python scripts/h545_window_guard.py --disable      # 停掉风险集
  python scripts/h545_window_guard.py --restore      # 全部重新启用（③ 判定后再做）
"""
from __future__ import annotations

import argparse
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 风险集：任务名 → (原定时刻, 为什么挡)
RISK = {
    "DSH_HFT_H443_JUDGE":   ("10:47", "`compound_ratio` 回滚会改单腿名义（×2）"),
    "DSH_HFT_H452_JUDGE":   ("11:25", "`trend_only_bp`；判定会重建 rollback ops 记录"),
    "DSH_HFT_H454_JUDGE":   ("11:46", "`trend_only_q` 回滚=关趋势闸（大幅改行为）"),
    "DSH_HFT_H356_JUDGE":   ("13:00", "宇宙试跑：可能改币种集合（最大的lane变更）"),
    "DSH_HFT_H435_JUDGE":   ("13:28", "`trail_lock_bp`"),
    "DSH_HFT_H436_JUDGE":   ("13:28", "`post_stop_decay`"),
    "DSH_HFT_H437_JUDGE":   ("13:28", "`p1_hold_sec`"),
    "DSH_HFT_H438_JUDGE":   ("13:28", "`p45_hold_sec`"),
    "DSH_HFT_H472_JUDGE":   ("14:33", "**干跑实测会 ROLLBACK**（engine_stall 21.6/h）"),
    "DSH_HFT_P2_JUDGE":     ("16:38", "P2 流向闸"),
    "DSH_HFT_H442_JUDGE":   ("22:17", "落在 ③ 的窗口（21:30→次日~09:30）内"),
}
# 必须保持启用
KEEP = ["DSH_HFT_H463_JUDGE", "DSH_HFT_H464_CHAIN", "DSH_HFT_LANE_ALARM",
        "DSH_MM_WORKER", "DSH_HFT_H472_MON2"]


def run(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def status(task: str) -> str:
    rc, out = run(["schtasks", "/query", "/tn", task, "/fo", "LIST"])
    if rc != 0:
        return "(不存在)"
    for line in out.splitlines():
        if line.strip().startswith("Status:"):
            return line.split(":", 1)[1].strip()
    return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--disable", action="store_true")
    g.add_argument("--restore", action="store_true")
    a = ap.parse_args()

    print("窗口守卫：保护 ② 的窗口（09:26→21:24）与 ③ 的窗口（21:30→次日 ~09:30）")
    print("=" * 84)
    print(f"{'任务':<24} {'时刻':<7} {'当前状态':<10} 原因")
    for t, (at, why) in RISK.items():
        print(f"{t:<24} {at:<7} {status(t):<10} {why}")
    print("\n保持启用（不受影响）：" + "、".join(KEEP))

    if not a.disable and not a.restore:
        print("\n⇒ DRY-RUN：未改动。`--disable` 停掉风险集；`--restore` 全部启用回来。")
        return 0

    action = "/change" if a.disable else "/change"
    for t in RISK:
        verb = "/disable" if a.disable else "/enable"
        rc, out = run(["schtasks", action, "/tn", t, verb])
        print(f"  {'禁用' if a.disable else '启用'} {t} ⇒ rc={rc} {out[:60]}")
    print("\n复核：")
    for t in list(RISK) + KEEP:
        print(f"  {t:<24} {status(t)}")
    if a.restore:
        print("\n⚠️ 重新启用后，这些试跑的窗口**仍含停机段** ⇒ 判决依旧作废；"
              "正确做法是同时**重开它们的窗口**（`--trial <k> --deploy --force`），"
              "或把它们排到 ③ 之后按单变量串行逐个重开。")
    else:
        print("\n⇒ ③ 的判定（次日 ~09:30）落地后，跑 `h545 --restore`，"
              "并按单变量串行逐个重开这些试跑的窗口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
