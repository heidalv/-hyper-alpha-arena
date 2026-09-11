# -*- coding: utf-8 -*-
"""[§85 核查 2026-09-11 / 目标①③ 同类横扫] "证据自锁"面：还有哪些闸门会**冻住自己的证据**？

背景（缺陷 #69，已修 P27-A）：出场通道熔断的抑制发生在记账之前 ⇒ 被抑制通道不再产生样本
⇒ 胜率永久冻结。修完这一处必须问：**同类闸门还有几个**？

判据（对每个闸门三问）：
  ① **证据**：它的状态由什么事件/统计决定？
  ② **自锁**：它拦的那个动作，是否正是产生该证据的动作？（拦 ⇒ 证据不再更新）
  ③ **自愈**：是否有 过期/衰减/探针/重启 之类的恢复路径？没有 ⇒ ❌ 自锁

本脚本把候选闸门列成表，并用**可自动检查的三条**做机器判定（源码级）：
  * 是否出现"新鲜度/staleness"类配置或函数（`stale`, `_age`, `hours`, `days` 组合）
  * 是否有"探针/放行一笔/reset/decay"路径（`probe`, `reset`, `decay`, `unfreeze`, `_recover`, `streak`）
  * 是否**不跨重启**（在只读内存里 ⇒ 重启即清）
其余由脚本输出待人工确认项，避免"自动化过度自信"。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

#: 候选闸门：(标识, 文件, 说明, 证据来源, 拦截动作, 是否与证据同源)
CANDIDATES = [
    ("出场通道熔断", "backend/services/exit/channel_breaker_gate.py",
     "按 tier|通道 滚动窗胜率抑制离场", "同通道的平仓事件", "抑制该通道的离场", True),
    ("来源信用 shadow", "backend/services/source_attribution.py",
     "按 src|nature|symbol 滚动窗净期望拦截开仓", "该来源的平仓事件", "拦截该来源的新开仓", True),
    ("组合回撤熔断", "backend/services/risk_management/portfolio_budget.py",
     "dd_sigma > 阈值 ⇒ 拒开仓（P17 已加 stale 自愈）", "已平仓交易的收益分布", "拒绝开仓", True),
    ("midlong 车道熔断", "backend/services/full_auto/midlong_circuit_gate.py",
     "车道级熔断：连续亏损/回撤 ⇒ 暂停开仓", "车道收益序列", "暂停该车道开仓", True),
    ("tier 熔断器", "backend/services/tier_circuit_breaker.py",
     "按 tier 记录连亏并熔断", "tier 的平仓结果", "暂停该 tier 交易", True),
    ("冻结协调器", "backend/services/risk_management/freeze_coordinator.py",
     "组合冻结/解冻（PB_FREEZE_ENABLED）", "回撤指标", "冻结开仓", True),
    ("再入场冷却", "backend/services/reentry_cooldown.py",
     "同一标的平仓后 N 秒/分钟不得再开", "平仓时间戳", "拦截该标的新开仓", True),
    ("融合 pwin 地板", "backend/services/learning_loop_service.py",
     "pwin 桶连续差 ⇒ 仲裁地板临时上提到 0.60", "仲裁后的实现胜率", "提高开仓门槛", True),
    ("活跃因子集", "backend/services/factor_engine/active_set_policy.py",
     "因子表现差 ⇒ 移出活跃集", "因子 IC/收益统计", "不再使用该因子", True),
    ("scalp shadow 模式", "backend/services/scalp/shadow_mode.py",
     "影子模式：只记录不下单", "配置/状态", "阻止实盘下单", False),
]


def _scan(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    low = src.lower()
    has_stale = bool(re.search(r"stale|_age_hours|evidence_stale|fresh", low))
    has_heal = bool(re.search(r"probe|reset|decay|unfreeze|_recover|recovery|streak|relax", low))
    has_restart_reset = bool(re.search(r"_ensure_loaded|不跨重启|清空|_loaded", src))
    has_ts = bool(re.search(r"last_ts|timestamp|time\.time\(\)|datetime", src))
    return {"exists": path.exists(), "stale": has_stale, "heal": has_heal,
            "restart": has_restart_reset, "ts": has_ts, "lines": src.count("\n") + 1}


def main() -> int:
    print("=" * 108)
    print("同类横扫：证据自锁面（拦截动作是否切断自己的证据 / 是否有自愈）")
    print("=" * 108)
    print(f"{'闸门':16s} {'证据同源':8s} {'新鲜度':7s} {'自愈路径':8s} {'重启重置':8s} {'时间戳':7s} {'行数':>6s} {'判定':>8s}")
    verdicts = []
    for name, rel, desc, ev, act, same_src in CANDIDATES:
        p = ROOT / rel
        f = _scan(p)
        if same_src:
            if f["stale"] or f["heal"] or f["restart"]:
                v = "可自愈"
            else:
                v = "❗自锁"
        else:
            v = "n/a"
        verdicts.append((name, rel, desc, ev, act, same_src, f, v))
        print(f"{name:16s} {('是' if same_src else '否'):8s} {('有' if f['stale'] else '—'):7s} "
              f"{('有' if f['heal'] else '—'):8s} {('有' if f['restart'] else '—'):8s} "
              f"{('有' if f['ts'] else '—'):7s} {f['lines']:>6d} {v:>8s}")

    print("\n【逐项证据（源码线索，需人工确认语义）】")
    for name, rel, desc, ev, act, same_src, f, v in verdicts:
        if v != "❗自锁":
            continue
        print(f"\n  ❗ {name}（{rel}）")
        print(f"     拦截：{act}｜证据：{ev}")
        print(f"     代码线索：新鲜度={f['stale']} 自愈={f['heal']} 重启重置={f['restart']} 时间戳={f['ts']}")
        print("     ⇒ 需人工确认：拦截是否真的切断证据？若无自愈路径 ⇒ 与缺陷 #69 同类")

    print("\n【自动化边界（避免过度自信）】")
    print("  * 上表只做**源码关键词**判定，结论必须逐项读代码确认（本脚本输出的是候选，不是判决）；")
    print("  * 已知真值（本轮已确认）：出场通道熔断 = 曾自锁（P27-A 已修）；")
    print("    来源信用 shadow = 有『重启放行一笔』的既有契约 ⇒ 可自愈；")
    print("    组合回撤熔断 = P17 已加 `PB_DD_STALE_HOURS` 自愈。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
