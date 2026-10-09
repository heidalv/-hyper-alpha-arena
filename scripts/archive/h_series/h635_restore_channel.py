# -*- coding: utf-8 -*-
"""h635 — 停摆事故处置 A：恢复开仓通道（三处**分别登记**的单变量改动）。

背景：`研究结论/停摆事故_20260929_1902.md`
  末腿 19:02（本地）起 4 小时 11 分零腿；`side_counts.none` = 全部决策。
  宇宙 18:58:56 被自动选币换成 [BNB,ETH]（BNB 在「只减仓」名单 ⇒ 实际只剩 ETH），
  再叠加 h622/h623/h624 的停开仓闸门 ⇒ 382/382 条决策被拦。
  数据链与 worker 均正常（不是断流、不是进程死）✗。

本脚本只动**三个**键（用户 2026-09-29 23:1x 选择 C：A+B 都做）：

  1) `trend_add_block_bp` 15.0 → 0.0
     为什么：`core.py:1359-1363`（h623 作者）自己写着「全局 30 会把正常波动的山寨
     一起停光（瘫痪事故）」，而线上是 **15**（该值的一半）。设计意图是「分位数 q 主导、
     bp 只作安静时段兜底」，实测 bp 成了主导闸（189/382 = 49.5%）⇒ 落回注释警告的
     失效模式。置 0 = 保留 q=0.9 的分位数闸（h623 本意仍在），只撤掉绝对下限。
  2) `jump_pause_sec` 60.0 → 10.0
     为什么：单步 12bp 就双边撤挂 **60 秒**；worker 约 2.3 s/拍 ⇒ 一次触发等于停 26 拍。
     跳价保护本身要留（设计建议 12~20 / 60），但 60 秒停摆与「≥60 腿/小时」硬约束冲突 ⇒
     保留阈值、把停摆从 60 s 收到 10 s。
  3) `markout_window_n` 10 → 0
     为什么：**自锁门闩**（本次新发现）——样本 `state.markout_samples` 持久化在
     SymbolState 里（`runner.py:198/237`），重启不清空；平仓时两侧都拦
     （`runner.py:2032-2038`）⇒ 没有新成交 ⇒ 样本永不更新 ⇒ `m30+cap<0` 永久成立
     ⇒ 永久零腿。实测：重启后 4 分钟内（当时零成交）该闸重新出现 30 次 ✓ 复现。
     置 window_n=0 = 关闸（代码注释：窗口 n=0 时闸关闭，与改动前一致）；探针
     `markout_horizon_sec=30` **保留** ⇒ 经济性照旧可观测，等"时间衰减 + 冷却探针"
     的持久修法预注册后再开闸。

**不做**的事：不动 `entry_block_symbols`（BNB 只减仓是有账本依据的设计 ✓）、
不动 `trend_add_block_q=0.9`、`be_mult`、`inv_add_block_ratio` 等其它工作流的试跑参数 ✓。

用法：
    python scripts/h635_restore_channel.py            # 只读：现状 + 将要做的事
    python scripts/h635_restore_channel.py --apply     # 写入（每条单独登记 ops_changes）
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services import lane_registry as reg  # noqa: E402

LANE = "mm_asterdex"

# (键, 目标值, 事故编号, 理由)
CHANGES: List[Tuple[str, Any, str, str]] = [
    ("trend_add_block_bp", 0.0, "停摆_1902",
     "core.py:1359-1363 记着 bp=30 的瘫痪事故，线上却是 15；实测该闸独占 49.5% 决策。"
     "置 0 保留 h623 的分位数闸 q=0.9（本意仍在），只撤绝对下限"),
    ("jump_pause_sec", 10.0, "停摆_1902",
     "12bp 单步即停 60 秒 ≈ 26 拍（2.3s/拍），与 ≥60 腿/小时硬约束冲突；"
     "保留 12bp 阈值与跳价保护，只把停摆收到 10 秒"),
    ("markout_window_n", 0, "停摆_1902",
     "自锁门闩：样本持久化在 SymbolState、重启不清空，平仓时两侧都拦 ⇒ 无新成交即永久锁死"
     "（重启后零成交仍在 4 分钟内复现 30 次）。置 0 = 关闸，探针 horizon=30 保留"),
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="写入注册表（默认只读预演）")
    ap.add_argument("--reason-suffix", default="",
                    help="附在理由后面的补充说明（如用户批准出处）")
    a = ap.parse_args()

    lane = reg.get_lane(LANE)
    if not lane:
        print(f"✗ 车道不存在: {LANE}")
        return 2
    meta = dict(lane.get("meta") or {})
    params = dict(meta.get("params") or {})

    print("=" * 92)
    print(f"h635 — 恢复开仓通道（{'写入' if a.apply else '只读预演'}）")
    print("=" * 92)
    print(f"  lane={LANE}  symbols={meta.get('symbols')}  h624_stage={meta.get('h624_stage')!r}")
    print(f"  entry_block_symbols={params.get('entry_block_symbols')}"
          "   ← 本脚本**不动**它（BNB 只减仓有账本依据）")

    todo = []
    for key, target, tag, why in CHANGES:
        cur = params.get(key, "<缺>")
        same = (cur == target) or (isinstance(cur, (int, float)) and isinstance(target, (int, float))
                                  and abs(float(cur) - float(target)) < 1e-12)
        mark = "✓ 已是目标值" if same else "→ 将改"
        print(f"\n  [{tag}] {key}: {cur!r} {mark}")
        print(f"      目标 = {target!r}")
        print(f"      理由 = {why}")
        if not same:
            todo.append((key, cur, target, tag, why))

    if not todo:
        print("\n  全部已是目标值 ⇒ 无需写入 ✓（幂等）")
        return 0
    if not a.apply:
        print(f"\n  --apply 未给 ⇒ 只预演，未写入（将改 {len(todo)} 个键）")
        return 0

    ops = meta.get("ops_changes")
    if not isinstance(ops, list):
        ops = []
    for key, before, target, tag, why in todo:
        params[key] = target
        ops.append({
            "ts": _now(), "by": "h635_restore_channel", "op": f"restore_channel_{key}",
            "field": f"params.{key}", "from": {key: before}, "to": {key: target},
            "tag": tag,
            "reason": why + (f"；{a.reason_suffix}" if a.reason_suffix else ""),
        })
    meta["params"] = params
    meta["ops_changes"] = ops[-20:]
    ok = reg.update_meta(LANE, meta)
    print(f"\n  update_meta ⇒ {ok}")

    after = ((reg.get_lane(LANE) or {}).get("meta") or {}).get("params") or {}
    print("  复核：")
    for key, _b, target, _tag, _why in todo:
        got = after.get(key)
        same = (got == target) or (isinstance(got, (int, float)) and isinstance(target, (int, float))
                                   and abs(float(got) - float(target)) < 1e-12)
        print(f"    params.{key} = {got!r}  {'✓' if same else '✗ 未生效'}")
    print("\n  [下一步] runner 是否热读这些 limits/params 需以心跳为准："
          "\n           python scripts/h632_registry_dump.py   # 看「注册表 vs 心跳」逐键对照")
    print("           不一致 ⇒ 用 python scripts/h218_restart_worker.py 重启 worker")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
