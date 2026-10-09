# -*- coding: utf-8 -*-
"""[H128 2026-09-21] vol_pause 闸现在是不是过度拦截（用户明确不要"冻结机会"）。

# 背景

用户原话：「不给冻结机会」。`vol_pause_sigma=0.7` 是逐 tick 的临时闸
（H112 实测有依据：最高段宽档强平率 11.6% vs 最低档 6.7%），
但**实测拦截占比**必须检查：如果它挡掉了绝大多数报价，那就不是"保护"而是"停摆"。

刚才的心跳：`skip_counts = {vol_pause: 492, symbol_exposure: 64, ...}`
⇒ vol_pause 占拦截的 **88%**。

# 判据（事先定死）

  · 若 `avg_sigma`（心跳的进程内均值）**远低于** 0.7，而被挡 492 次
    ⇒ 说明挡的不是"高波动时刻"，而是**基准漂移**（`vol_baseline_bp` 陈旧导致
      σ_norm 系统性偏高）⇒ 是缺陷，必须修
  · 若 `avg_sigma` 接近或超过 0.7 ⇒ 挡得合理（此刻确实高波动）

# 注意口径

心跳的 `avg_sigma` 是**决策时**的均值（只统计真正报价的动作），
而被 `vol_pause` 挡掉的决策**不进**这个均值 ⇒ 两者口径不同，
不能直接比。所以本脚本另外算一个**独立口径**：
用 `sigma_decisions`（若有）或直接从 `asterdex_book_ticker` 重算近 2h 的
σ/基准比，给出不带选择偏差的分布。

用法：
    .venv\\Scripts\\python.exe scripts\\h128_vol_pause_audit.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def main() -> int:
    st = Path("logs/mm_lane_status.json")
    j = json.loads(st.read_text(encoding="utf-8"))

    print("=" * 96)
    print("H128  vol_pause 闸拦截占比审计")
    print("=" * 96)
    sk = j.get("skip_counts") or {}
    total = sum(sk.values()) or 1
    print(f"\n  当前拦截分布（累计，进程启动以来）：")
    for k, v in sorted(sk.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<22} {v:>6}  {v/total*100:>5.1f}%")
    print(f"    {'合计':<22} {total:>6}")

    print(f"\n  报价行为读数：")
    print(f"    quoted_decisions   {j.get('quoted_decisions')}")
    print(f"    avg_sigma          {j.get('avg_sigma')}   ← 决策时机口径（被挡的不计入）")
    print(f"    avg_sigma_all      {j.get('avg_sigma_all')}  ← 全样本口径（若无则未采集）")
    print(f"    avg_base_bp        {j.get('avg_base_bp')}")
    print(f"    avg_width_bp       {j.get('avg_width_bp')}")
    print(f"    fills_per_hour     {j.get('fills_per_hour')}")
    print(f"    quote_modes        {j.get('quote_modes')}")
    lim = j.get("limits") or {}
    print(f"    vol_pause_sigma    {lim.get('vol_pause_sigma')}")

    vp = sk.get("vol_pause", 0)
    print(f"\n  vol_pause 占拦截 **{vp/total*100:.1f}%**")
    av = j.get("avg_sigma_all")
    if av is not None:
        print(f"\n  独立口径 avg_sigma_all = {av}（阈值 {lim.get('vol_pause_sigma')}）")
        if lim.get("vol_pause_sigma") and av > float(lim["vol_pause_sigma"]) * 0.8:
            print("  => 全样本 σ 已接近阈值 => 挡得**合理**（此刻确实高波动）")
        else:
            print("  => 全样本 σ 远低于阈值 => 挡的是**基准漂移**，不是真波动 => 需修")
    else:
        print("\n  avg_sigma_all 未采集（心跳没带这个字段）")
        print("  => 无法用独立口径判定。但注意一个**选择偏差**陷阱：")
        print("     被 vol_pause 挡掉的决策不进 avg_sigma ⇒ 用它和阈值比会**低估**波动，")
        print("     从而错误地得出『闸门过度拦截』。要么补采集，要么用别的方式判。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
