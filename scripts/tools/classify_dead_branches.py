"""Classify the never-triggered branches found by `gate_inventory.py`.

For each string that exists in source but has NEVER appeared at runtime,
read the code context and classify it:

  E = error path      (guarded by `except`, or name implies failure)
  M = transient msg   (one-shot config/startup message, not per-tick)
  D = legacy / dead   (retired mechanism, superseded by another path)
  ? = UNEXPLAINED     (needs investigation -- could be a broken mechanism)

`?` entries are the only ones worth acting on. Everything else is expected.
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
MM = ROOT / "backend" / "services" / "market_maker"

ERROR_HINT = re.compile(
    r'error|fail|失败|divergence|no_quote|no_mid|no_book|no_situation|'
    r'no_evidence|429|venue_|read_error|reconcile', re.I)
LEGACY_HINT = re.compile(
    r'timeout_hard_taker|timeout_taker|trail_lock|trend_flip|jump_exit|'
    r'reversal_decay|ofi_flatten|take_profit|stop_loss_maker|sudden_move|'
    r'situation_ok|situation_probe|ok$|reset_align', re.I)
MSG_HINT = re.compile(r'evolution|初始化|全局最优|网格|主动流', re.I)

# ── [T48] 已人工查证并记录的"正确沉默"清单 ─────────────────────────────
# 每条都写明**为什么它不该触发**。这不是"消除警告"，是把结论固化：
# 下次谁看到这些名字，不必再重查一遍。
EXPLAINED_SILENT = {
    "dust_swept": "条件是名义 < $5；现腿量约 $500（100 倍）⇒ 永不满足（正确）",
    "mu_below_margin": "仅当 `not _is_probe and not explore_entry` 才可能触发，"
                       "而引擎恒在探针模式 ⇒ 天然不可达（正确，但记录："
                       "安全垫在探针模式下不参与）",
    "flow_reversal": "需要持仓状态下反向流；实测 24h 仅 2 次 ⇒ 极罕见（正确）",
    "holding(active_flow)": "需 keep_working 命中后的分支；与 skip `holding`(9 次) "
                            "是不同代码点 ⇒ 罕见（正确）",
    "jump_pause": "跳价暂停需单 tick 急跳；当前波动低 ⇒ 罕见（正确）",
    "no_op": "live_bridge 的显式空操作（设计如此）",
    "pause": "需 action==pause 的显式路径（设计如此）",
    "explore_negative_bucket": "需门开到「负桶」；当前全程探针 ⇒ 罕见（正确）",
    "maker_working(": "这是 f-string 前缀（实为 `maker_working(buy/sell)`），"
                      "运行时以 `maker_working` 出现 ⇒ 工具口径（正常）",
    "situation_ok": "situation 表 0 个可用档 ⇒ 该成功分支不触发（正确）",
    "situation_probe": "同上，探针分支被 gap_soft_probe 取代（正确）",
    "ok": "flow_rules 里的成功返回（非 skip）⇒ 口径（正常）",
    "reset_align": "仅在重置对齐时触发（一次性）⇒ 罕见（正确）",
    "sudden_move": "需单 tick 急动超阈；低波动期不触发（正确）",
    # ── [T48 复核] 最后 4 项，逐个查证 ──
    "taker_chase": "吃单追进场（h879）。T2 已证明它负期望（均 net −6.25bp）并加了"
                   "成本门槛 ⇒ 实测 `flow_entry_taker` 119 → 0 ⇒ **按设计不再触发**（正确）",
    "无候选（全窗口均不劣于在位的候选为空）": "evolution.py 的「无候选」文案；"
                                              "为一次性进化日志，非每拍信号（正常）",
    "窗口数据不足": "同上的窗口不足文案（正常）",
    "用户在前端重置车道专属模拟账户": "runner.py:4757 的**审计日志**文本，"
                                      "不是 skip/reason 信号 ⇒ 口径（正常）",
}

# strings appearing in the status dump but under a different key
STATUS_KEYS = {"maker_working", "maker_edge", "maker_risk", "holding"}


def main() -> int:
    inv = json.loads(
        (ROOT / "logs" / "_gate_inventory.json").read_text(encoding="utf-8"))
    dead = inv["dead"]
    print("=" * 96)
    print(f"never-triggered branches: {len(dead)}")
    print("=" * 96)

    buckets: dict = {"E": [], "M": [], "D": [], "S": [], "?": []}
    for name, loc in dead:
        fname, _, lineno = loc.partition(":")
        try:
            lines = (MM / fname).read_text(
                encoding="utf-8", errors="replace").splitlines()
            ln = int(lineno)
            ctx = "\n".join(lines[max(0, ln - 14):ln + 2])
        except Exception:  # noqa: BLE001
            ctx = ""
        if name in EXPLAINED_SILENT:
            buckets["S"].append((name, loc))
        elif ERROR_HINT.search(name) or re.search(r'except\b[^\n]*:\s*\n', ctx):
            buckets["E"].append((name, loc))
        elif MSG_HINT.search(name) or MSG_HINT.search(ctx):
            buckets["M"].append((name, loc))
        elif LEGACY_HINT.search(name):
            buckets["D"].append((name, loc))
        else:
            buckets["?"].append((name, loc))

    titles = {
        "E": "【E】错误路径 —— 本就不该触发（正常）",
        "M": "【M】一次性/配置消息 —— 非每拍路径（正常）",
        "D": "【D】历史机制 / 已被其它路径取代（需确认）",
        "S": "【S】已人工查证的「正确沉默」（附理由）",
        "?": "【?】**未解释 —— 需要调查**（可能是坏掉的机制）",
    }
    for k in ("E", "M", "D", "S", "?"):
        v = buckets[k]
        print()
        print(f"{titles[k]}   共 {len(v)} 个")
        if not v:
            continue
        for name, loc in sorted(v):
            print(f"    {name[:46]:<48}{loc}")
            if k == "S":
                print(f"        ↳ {EXPLAINED_SILENT.get(name, '')}")

    print()
    print("=" * 96)
    print("结论")
    print("=" * 96)
    print(f"  正常（错误路径+消息+历史）: {len(buckets['E'])+len(buckets['M'])+len(buckets['D'])}")
    print(f"  待查（未解释）            : {len(buckets['?'])}")
    if buckets["?"]:
        print("  ⚠️ 下面这些必须逐个确认：它们可能是'我想要、但它没在跑'的机制")
        for name, loc in sorted(buckets["?"]):
            print(f"      - {name} @ {loc}")
    else:
        print("  ✅ 所有未触发分支都有合理解释")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
