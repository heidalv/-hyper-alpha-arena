# -*- coding: utf-8 -*-
"""H421 P2 提案桥：h269 监控建议 → 结构化提案卡 → h375 队列。

设计对应《随市进化_可执行性报告_20260927.md》P2（LLM 提案→试跑队列桥）。
安全边界（设计 §3.3/§7）：
  · LLM/规则建议**永不直改参数**——本桥只产出提案卡，实际进化仍走既有试跑
    脚本与守卫链（预注册→影子→12h→Welch→自动回滚）；
  · 单日提案上限 MAX_PER_DAY（防洪泛）；同类型同日去重（state 文件）；
  · 未映射到既有候选的建议标 needs_review=True（人工评审），不自动入队。

映射表（建议类型 → 既有候选/试跑）：
  hard_stop_overactive → #17 尾随锁利（h389）
  exit_too_deep        → #18 宽限归零（h392）/#16③ 降腿量（h396）
  vol_pause_high       → 提案"vol_pause_sigma 上调"（needs_review）
  symbol_bleeding      → #2 分币种摘除（h356 judge 已有机制）
  LLM param_advice     → 通用提案卡（needs_review=True）

用法: python scripts/h421_proposal_bridge.py [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SUG = ROOT / "research_l1" / "out" / "h269b_suggestions.jsonl"
LLM = ROOT / "research_l1" / "out" / "h269_llm_monitor.jsonl"
STATE = ROOT / "research_l1" / "out" / "h421_proposal_state.json"
BACKLOG = ROOT.parent / "研究结论" / "h375_phase2_backlog_20260927.md"
MAX_PER_DAY = 5
AGE_HOURS = 2.0   # 只桥接最近 2h 内的建议（新鲜度）

MAP = {
    "hard_stop_overactive": {
        "candidate": "#17 尾随锁利（trail_lock_bp=20）",
        "trial": "h389_trail_lock_trial.py", "needs_review": False,
    },
    "exit_too_deep": {
        "candidate": "#18 宽限归零（stop_maker_grace_sec 0）/ #16③ 降腿量",
        "trial": "h392_grace_zero_trial.py / h396_post_stop_decay_trial.py",
        "needs_review": False,
    },
    "vol_pause_high": {
        "candidate": "提案：vol_pause_sigma 上调（无既有试跑）",
        "trial": None, "needs_review": True,
    },
    "symbol_bleeding": {
        "candidate": "#2 分币种摘除机制（h356 judge）",
        "trial": "h356_universe_trial.py --judge", "needs_review": False,
    },
}


def _tail_jsonl(path: pathlib.Path):
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    day = now.strftime("%Y-%m-%d")
    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    emitted_today = [e for e in (state.get("emitted") or [])
                     if e.get("day") == day]

    cards = []
    # 规则建议
    sug = _tail_jsonl(SUG)
    if sug:
        at = sug.get("at") or ""
        try:
            age_h = (now - dt.datetime.fromisoformat(at)).total_seconds() / 3600.0
        except Exception:
            age_h = 999
        if age_h <= AGE_HOURS:
            for s in sug.get("suggestions") or []:
                typ = s.get("type") or ""
                key = f"{day}:{typ}"
                if any(e["key"] == key for e in emitted_today):
                    continue
                m = MAP.get(typ, {"candidate": "未映射", "trial": None,
                                  "needs_review": True})
                cards.append({"key": key, "type": typ, "evidence": s.get("advice") or "",
                              "candidate": m["candidate"], "trial": m["trial"],
                              "needs_review": m["needs_review"]})
    # LLM param_advice（必须新鲜：旧提示词的陈旧输出会引用已废弃框架）
    llm = _tail_jsonl(LLM)
    if llm and isinstance(llm.get("llm"), dict):
        llm_at = ((llm.get("data") or {}).get("at")) or ""
        try:
            llm_age_h = (now - dt.datetime.fromisoformat(llm_at)).total_seconds() / 3600.0
        except Exception:
            llm_age_h = 999
        llm_fresh = llm_age_h <= AGE_HOURS
        if not llm_fresh:
            print(f"[跳过 LLM 建议：输出陈旧 {llm_age_h:.1f}h（> {AGE_HOURS}h 时间门）]")
        if llm_fresh:
            for s in llm["llm"].get("symbols") or []:
                adv = (s or {}).get("param_advice") or ""
                if not adv:
                    continue
                key = f"{day}:llm:{s.get('symbol', '?')}"
                if any(e["key"] == key for e in emitted_today):
                    continue
                cards.append({"key": key, "type": "llm_param_advice",
                              "evidence": f"[{s.get('symbol')}] {adv[:160]}",
                              "candidate": "通用提案（待人工映射）", "trial": None,
                              "needs_review": True})
            g = llm["llm"].get("global") or {}
            if g.get("top_action"):
                key = f"{day}:llm:top"
                if not any(e["key"] == key for e in emitted_today):
                    cards.append({"key": key, "type": "llm_top_action",
                                  "evidence": str(g["top_action"])[:200],
                                  "candidate": "通用提案（待人工映射）", "trial": None,
                                  "needs_review": True})

    cards = cards[:MAX_PER_DAY]
    print(f"提案卡 {len(cards)} 张（限流 {MAX_PER_DAY}/日，去重后）")
    for c in cards:
        flag = "⚠评审" if c["needs_review"] else "可入队"
        print(f"  [{flag}] {c['type']}: {c['evidence'][:70]}")
        print(f"        → {c['candidate']}"
              + (f"（试跑：{c['trial']}）" if c["trial"] else "（无既有试跑）"))

    if not cards:
        print("无新提案（近 2h 无建议或均已去重）")
        return 0

    if not a.dry_run:
        sec = [f"\n## [h421 提案桥 · {now:%Y-%m-%d %H:%M}] 监控提案卡（P2 桥，只入队不执行）\n"]
        for c in cards:
            sec.append(
                f"- {c['type']}：{c['evidence']} → {c['candidate']}"
                + (f"（试跑 {c['trial']}）" if c["trial"] else "")
                + ("【needs_review：人工评审后决定】" if c["needs_review"] else ""))
        if BACKLOG.exists():
            with open(BACKLOG, "a", encoding="utf-8") as f:
                f.write("\n".join(sec) + "\n")
        emitted_today.extend({"key": c["key"], "type": c["type"]} for c in cards)
        state["emitted"] = (state.get("emitted") or [])[-200:] + \
            [{"key": c["key"], "day": day} for c in cards]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        print(f"已写入 {BACKLOG.name} + 去重状态 {STATE.name}")
    else:
        print("[dry-run] 未写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
