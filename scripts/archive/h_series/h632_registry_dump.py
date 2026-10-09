"""h632 — mm_asterdex 的**权威注册表** vs **运行时真值** 对照（只读，R94）。

为什么要这个：2026-09-29 23:07 重启 worker 后实测"报价被 100% 拦掉"
（`side_counts.none=128` = 64 拍 × 2 币），三个闸门合计正好等于全部决策数：
`jump_pause` 44 + `trend_add_block` 44 + `entry_block` 40 = 128 ✗。
而 h631 的限额白名单里**没有**这三把闸门的阈值 ⇒ 必须去权威源读。

权威源（F327）：`lane_registry.meta_json`（Postgres），`meta.params` 是参数、
`meta.symbols` 是宇宙、`meta.ops_changes` 是变更登记（**只保留最近 20 条** ✗）。

本脚本只读，不改任何东西。三件事一起打：
  1) 宇宙（symbols）+ h624 阶段标记 —— "为什么只剩 BNB/ETH" 的答案在这里
  2) 三把闸门的阈值 + 宇宙/参数全量
  3) 注册表 vs 心跳真值逐键对照 —— 不一致的键单独列出（调参变猜谜的根源）
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATUS = ROOT / "logs" / "mm_lane_status.json"

# 三把闸门 + 宇宙/开关相关的键（先看这些，再看全量）
GATE_KEYS = (
    "jump_pause_bp", "jump_pause_sec",
    "trend_add_block_bp", "trend_add_block_q",
    "entry_block_symbols",
)
H624_KEYS = (
    "markout_horizon_sec", "markout_window_n", "markout_halt_bp",
    "be_mult", "be_taker_fee_bp", "jump_pause_bp", "jump_pause_sec",
)


def _line(t: str) -> None:
    print("\n" + "-" * 90)
    print(t)
    print("-" * 90)


def main() -> int:
    import os
    print("=" * 90)
    print("h632 — lane_registry(mm_asterdex) 权威配置 vs 运行时心跳")
    print("=" * 90)
    print(f"  MM_REGISTRY_AUTHORITATIVE = "
          f"{os.getenv('MM_REGISTRY_AUTHORITATIVE')!r}"
          "   （1 = 注册表权威；空/0 = env 或默认值可能压过注册表 ✗）")

    try:
        from backend.services import lane_registry as r
        lane = r.get_lane("mm_asterdex")
    except Exception as e:
        print(f"✗ 读注册表失败：{type(e).__name__}: {e}")
        return 1
    if not lane:
        print("✗ 注册表里没有 mm_asterdex")
        return 1

    meta = dict(lane.get("meta") or {})
    params = dict(meta.get("params") or {})
    lim = dict(meta.get("limits") or {})
    syms = list(meta.get("symbols") or [])

    _line("1) 宇宙与阶段标记")
    print(f"  mode          = {lane.get('mode')!r}   status = {lane.get('status')!r}")
    print(f"  updated_at    = {lane.get('updated_at')!r}")
    print(f"  meta.symbols  = {syms}   （共 {len(syms)} 币）")
    print(f"  meta.h624_stage = {meta.get('h624_stage')!r}")
    print(f"  meta 顶层键   = {sorted(meta.keys())}")

    _line("2) 三把闸门（重启后 100% 拦截的三个标签）")
    for k in GATE_KEYS:
        src = "params" if k in params else ("limits" if k in lim else "✗ 两处都没有")
        v = params.get(k, lim.get(k))
        print(f"  {k:24s} = {json.dumps(v, ensure_ascii=False):>28}   [{src}]")

    _line("3) h624 阶段参数")
    for k in H624_KEYS:
        v = params.get(k, lim.get(k, "<缺>"))
        print(f"  {k:24s} = {v!r}")

    _line("4) 注册表 params 全量")
    print(json.dumps(params, ensure_ascii=False, indent=2, sort_keys=True))

    _line("5) 注册表 limits 全量（若非空）")
    if lim:
        print(json.dumps(lim, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("  （meta.limits 为空 ⇒ limits 走代码默认值/其它来源）")

    _line("6) ops_changes（最近 20 条，登记表只留这么多 ✗）")
    ops = list(meta.get("ops_changes") or [])
    if not ops:
        print("  （无）")
    for op in ops[-20:]:
        ts = str(op.get("ts") or "")
        keys = [k for k in op.keys() if k not in ("ts", "by", "note")]
        print(f"  {ts:26s} {json.dumps({k: op[k] for k in keys}, ensure_ascii=False)[:150]}")

    _line("7) 注册表 vs 运行时心跳（不一致 = 读数陷阱）")
    if not STATUS.exists():
        print("  ✗ 心跳文件不存在")
        return 0
    st = json.loads(STATUS.read_text(encoding="utf-8", errors="replace"))
    rt_lim = dict(st.get("limits") or {})
    rt_par = dict(st.get("params") or {})
    for k in GATE_KEYS + H624_KEYS:
        reg = params.get(k, lim.get(k, "<缺>"))
        rt = rt_par.get(k, rt_lim.get(k, "<缺>"))
        same = (reg == rt) or (isinstance(reg, float) and isinstance(rt, float)
                               and abs(reg - rt) < 1e-12)
        mark = "✓" if same else "✗ 不一致"
        print(f"  {k:24s} 注册表={json.dumps(reg, ensure_ascii=False):>26}"
              f"  心跳={json.dumps(rt, ensure_ascii=False):>26}  {mark}")
    rt_syms = st.get("symbols")
    print(f"  {'symbols':24s} 注册表={json.dumps(syms, ensure_ascii=False):>26}"
          f"  心跳={json.dumps(rt_syms, ensure_ascii=False):>26}  "
          f"{'✓' if list(rt_syms or []) == syms else '✗ 不一致'}")

    print("\n" + "=" * 90)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
