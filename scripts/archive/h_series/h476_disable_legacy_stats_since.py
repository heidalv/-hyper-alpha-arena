"""h476：**禁止 legacy 试跑脚本改写 `meta.stats_since`**（账户口径只能由"重置账户"改变）。

事故链（用户两次投诉"手续费又被重置"，本次复发已定位）：
  · `/api/hft/account` 的**数值**用的是 `account_reset_at or stats_since`（正确），
    但**展示的边界** `book.since` 用的是 `stats_since`（hft_routes.py:1041）
    ⇒ 只要 stats_since 被改写，面板上"本时代起点"就跳一次，看起来就是"又被重置"。
  · 本次复发来源：`DSH_HFT_H392_JUDGE @ 02:37L` 跑的是 legacy 脚本
    `scripts/h392_grace_zero_trial.py`，其 rollback 分支写
    `meta["stats_since"] = now_iso` ⇒ 边界跳到 2026-09-28T18:37:01Z。
  · **不只是显示**：`stats_since` 同时是**日亏闸的裁剪依据**
    （`lane_day_pnl_usd` 按 `max(当日0点, stats_since)` 裁剪）⇒ 前移会**少算当日亏损、
    放松风控**。所以必须从源头禁写。

本脚本把以下 legacy 脚本里的 `meta["stats_since"] = ...` 赋值改成 `pass`（保留缩进与
块结构，语法等价、行为=不再改写），并逐个 `ast.parse` 校验语法：

  h354_p2_judge / h354_p2_deploy / h356_universe_trial / h357_flow_gate_trial
  h359_p2v2_trial / h362_model_gate_trial / h363_p45_trial / h389_trail_lock_trial
  h392_grace_zero_trial / h396_post_stop_decay_trial / h399_mp_gate_trial
  h400_p1_trigger_trial / h401_p3_spike_trial / h404_pattern_hold_trial
  h406_bnb_momentum_trial / h411_timeout_hard_taker_trial

（现行框架 `h425_repair_trial.py` 早已不写 stats_since，不动它。）

用法：python scripts/h476_disable_legacy_stats_since.py [--apply]
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = [
    "h354_p2_judge.py", "h354_p2_deploy.py", "h356_universe_trial.py",
    "h357_flow_gate_trial.py", "h359_p2v2_trial.py", "h362_model_gate_trial.py",
    "h363_p45_trial.py", "h389_trail_lock_trial.py", "h392_grace_zero_trial.py",
    "h396_post_stop_decay_trial.py", "h399_mp_gate_trial.py",
    "h400_p1_trigger_trial.py", "h401_p3_spike_trial.py",
    "h404_pattern_hold_trial.py", "h406_bnb_momentum_trial.py",
    "h411_timeout_hard_taker_trial.py",
]
PAT = re.compile(r'^(?P<ind>\s*)meta\[(?P<q>["\'])stats_since(?P=q)\]\s*=\s*(?P<rhs>.+?)\s*$')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认只预演）")
    a = ap.parse_args()
    total = 0
    for name in FILES:
        p = ROOT / "scripts" / name
        if not p.exists():
            print(f"  ✗ 缺文件 {name}")
            continue
        src = p.read_text(encoding="utf-8")
        hits = 0
        out_lines = []
        for ln in src.splitlines():
            m = PAT.match(ln)
            if m:
                hits += 1
                out_lines.append(
                    f'{m.group("ind")}pass  # [h476 2026-09-29 禁用] '
                    f'was: meta["stats_since"] = {m.group("rhs")}')
            else:
                out_lines.append(ln)
        if not hits:
            print(f"  · {name}: 无需改（无 stats_since 赋值）")
            continue
        new = "\n".join(out_lines) + ("\n" if src.endswith("\n") else "")
        try:
            ast.parse(new)
        except SyntaxError as exc:  # 语法必须保持
            print(f"  ✗ {name}: 改写后语法错误 {exc} ⇒ 跳过")
            continue
        total += hits
        print(f"  ✓ {name}: 禁用 {hits} 处 stats_since 赋值")
        if a.apply:
            p.write_text(new, encoding="utf-8")
    print(f"\n{'已应用' if a.apply else '预演'}：共 {total} 处")
    if not a.apply:
        print("加 --apply 真正写盘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
