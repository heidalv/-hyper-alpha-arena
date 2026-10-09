"""h619 — **旋钮可达性审计**（只读；R212）：队列里每个参数，到底能不能产生行为？

由来：R201 实测 `ofi_confirm_threshold` **被代码消费、却结构性地永远不会生效** ✗
（分支带 `and allow_*` 守卫，被更早的趋势闸抢先 ⇒ 3500 次拦截里 0 次）。
这类"死旋钮"如果没被发现，就会**白烧一个 12h 判定窗**再得出 INCONCLUSIVE ✗。
本脚本把"这根旋钮活着吗"变成**可重复的读数**，对**队列里剩下的每一项**都适用 ✓。

对每个参数查三件事：
  ① **代码里有没有消费点**（在 `backend/` 非测试代码里出现的次数）；
  ② **有没有对应的可观测计数器**，以及**它现在动不动**（读 worker 心跳）；
  ③ 已知的**抢先/守卫**风险（人工维护的备注，来自本项目已查明的结论）。

用法：python scripts/h619_knob_reachability.py
"""
from __future__ import annotations

import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATUS = ROOT / "logs" / "mm_lane_status.json"
BACKEND = ROOT / "backend"
SKIP = ("tests", "test_")

# (参数, 心跳计数器键(或 None), 备注：已知的守卫/抢先风险)
ROWS = [
    ("max_one_side_seconds", None,
     "②正在跑（加仓窗口）；无独立计数器 ⇒ 看腿/趟与腿速"),
    ("trend_only_q", "trend_only_flat",
     "⑥ 拟改（0.35→0）；**它正是抢先别的那道闸** ⇒ 一动就影响面很大 ✓"),
    ("trend_pause_bp", "trend_up/trend_down",
     "绝对值闸；=15 ⇒ |20 期趋势|≥15bp 就封加仓侧（**就是它把 confirm 闸抢先了** ✗）"),
    ("max_quote_age_sec", "stale_quote_cleared",
     "⑤ 拟改（90→45）；计数器稀疏（历史读数 1–2/10min）⇒ 改动效果可能很小 ✓"),
    ("ofi_confirm_threshold", "ofi_confirm_buy/ofi_confirm_sell",
     "**死旋钮**（R201）：带 `and allow_*` 守卫、被趋势闸抢先 ⇒ 实测 0 次 ✗；③ 已改为 h529"),
    ("ofi_require_threshold", "gate_probe_counts",
     "③′ 新字段（h529）；探针 `ofi_require_hit/blocked` 与遮蔽无关 ✓；默认 0=关闭"),
    ("ofi_block_threshold", "ofi_toxic_buy/ofi_toxic_sell",
     "禁令族；实测 **在动**（ofi_toxic_sell=181/2.6h）✓"),
    ("per_symbol_size_mult", None,
     "④ 拟部署；账本侧看逐币单笔名义 P50（h527 判据）✓"),
    ("ofi_flatten_maker_only", None,
     "h472 已部署；看出场腿 taker 占比（h546 的 taker_mix）✓"),
    ("reversal_decay_bp", "reversal_decay",
     "① 的出场路径；计数器在动（纪元内 14 腿）✓ ⇒ ① 的'保留'是持续性结论"),
]


def consumed(field: str) -> int:
    """在 `backend/` 非测试代码里数该字段的出现次数（消费点的下界）。"""
    n = 0
    for p in BACKEND.rglob("*.py"):
        sp = str(p)
        if any(s in sp for s in SKIP):
            continue
        try:
            n += p.read_text(encoding="utf-8", errors="replace").count(field)
        except Exception:  # noqa: BLE001
            continue
    return n


def main() -> int:
    print("=" * 100)
    print("h619 — 旋钮可达性审计（只读）：队列里的参数能不能产生行为？")
    print("=" * 100)
    try:
        raw = json.loads(STATUS.read_text(encoding="utf-8"))
        sk = dict(raw.get("skip_counts") or {})
        gp = dict(raw.get("gate_probe_counts") or {})
        import time as _t
        age = _t.time() - float(raw.get("ts") or 0.0)
        print(f"  心跳年龄 {age:.1f}s（{'新鲜 ✓' if 0 <= age <= 90 else '陈旧 ✗'}）\n")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 心跳不可读：{type(e).__name__}")
        return 1

    print(f"  {'参数':<26}{'消费点':>6}  {'计数器现值':<34}备注")
    print("-" * 100)
    for field, counter, note in ROWS:
        c = consumed(field)
        if counter is None:
            cur_s = "（无独立计数器）"
        elif counter == "gate_probe_counts":
            cur_s = f"probe={gp if gp else '（键不存在：worker 未重启）'}"
        else:
            parts = []
            for k in str(counter).split("/"):
                parts.append(f"{k}={int(sk.get(k) or 0)}")
            cur_s = "、".join(parts)
        mark = "✓" if c > 0 else "✗"
        print(f"  {field:<26}{mark}{c:>5}  {cur_s:<34}{note}")

    print("\n" + "-" * 100)
    print("  读法：**消费点 >0 只是必要条件**（R201 的教训：消费了也可能永远不生效 ✗）；")
    print("        必须再看**计数器动不动** —— 0 次而有消费点 ⇒ 先怀疑被更早的闸抢先，")
    print("        而不是先归因于市场（这正是本审计存在的理由 ✓）。")
    print("  ⇒ 队列里**最该先做的是 ⑥（trend_only_q）**：它既是抢先别的那道闸、又是活闸 ✓；")
    print("     而 **⑤（max_quote_age_sec）的计数器很稀疏** ⇒ 预期效果小，优先级可后置 ✓。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
