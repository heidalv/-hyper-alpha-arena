# -*- coding: utf-8 -*-
"""四系统耦合现状体检（只读）：因子 / LLM 策略 / 新闻 / 主脑。

口径：**只统计最近一次后端重启之后的事件**（边界=日志里最后一次 `Application startup complete.`），
避免用重启前的旧结论冒充现状。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

p = ROOT / "logs" / "backend.log"
with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - 20_000_000))
    raw = f.read().decode("utf-8", errors="replace").splitlines()

marks = [ln[:19] for ln in raw if "Application startup complete." in ln]
RS = marks[-1] if marks else ""
post = [ln for ln in raw if ln[:19] >= RS] if RS else raw

print("=" * 96)
print(f"四系统耦合体检：窗口 = {RS or '(无重启标记, 用全窗口)'} → {raw[-1][:19] if raw else '?'}")
print(f"窗口内日志行数 = {len(post):,}")
print("=" * 96)

def cnt(pred, rows=None):
    return sum(1 for ln in (rows if rows is not None else post) if pred(ln))

def show(title, items):
    print(f"\n【{title}】")
    for name, n in items:
        flag = "✅" if n else "—"
        print(f"   {flag} {name:44s} {n}")

# ── 1. 因子系统 → 决策 ──
fr = [ln for ln in post if "FactorRouteAB" in ln or "FactorRoute]" in ln]
fr_votes = Counter()
for ln in fr:
    m = re.search(r"n=(\d+)", ln)
    if m:
        fr_votes[int(m.group(1))] += 1
show("① 因子系统 → 决策（factor_route）", [
    ("[FactorRouteAB] 决策行", len(fr)),
    ("其中 opened=True", cnt(lambda l: "opened=True" in l and "FactorRoute" in l)),
    ("gate=position_exists（已有仓）", cnt(lambda l: "gate=position_exists" in l)),
    ("因子投票数 n=16 的行", fr_votes.get(16, 0)),
    ("[FactorEngine] 因子计算日志", cnt(lambda l: "factor_calculator" in l)),
    ("[FactorEngine] 未分类因子告警", cnt(lambda l: "未知因子类型" in l)),
])

# ── 2. LLM 策略系统 ──
show("② LLM 策略系统（含备用车道）", [
    ("call_ai_for_decision*", cnt(lambda l: "call_ai_for_decision" in l)),
    ("PromptContextBuilder / prompt_context", cnt(lambda l: "prompt_context" in l.lower())),
    ("synthesize（主控合成）", cnt(lambda l: "synthesize" in l)),
    ("analyst_fallback / analyst_error", cnt(lambda l: "analyst_fallback" in l or "analyst_error" in l)),
    ("LLM Fallback / 降级规则引擎", cnt(lambda l: "LLM Fallback" in l or "降级" in l)),
    ("AI-Decision 生成", cnt(lambda l: "AI-Decision" in l or "ai_decision" in l)),
    ("[LLM] 配置拒绝（多租户）", cnt(lambda l: "拒绝公用默认配置" in l)),
    ("LLM 流式调用", cnt(lambda l: "[LLM sync" in l or "[LLM sync stream]" in l)),
    ("MidLongAgent 独立 tick", cnt(lambda l: "MidLongAgent独立" in l)),
])

# ── 3. 新闻系统 → prompt ──
show("③ 新闻系统 → prompt", [
    ("news 相关日志", cnt(lambda l: "news" in l.lower() and "service" in l.lower())),
    ("[News] / 新闻 字样", cnt(lambda l: "[News]" in l or "新闻" in l)),
    ("news_section 占位符渲染", cnt(lambda l: "news_section" in l)),
    ("渲染成 N/A 的行", cnt(lambda l: "N/A" in l)),
    ("macro_data_collector / 日历", cnt(lambda l: "macro_data_collector" in l or "日历" in l)),
    ("whale_tracker (鲸鱼)", cnt(lambda l: "whale_tracker" in l)),
])

# ── 4. 主脑系统 ──
bl = [ln for ln in post if "MidLongBrain" in ln]
skip = Counter()
for ln in bl:
    m = re.search(r"reason=([^\s]+)", ln)
    if m and "skip open" in ln:
        skip[m.group(1)] += 1
show("④ 主脑（MLTO）", [
    ("[MidLongBrain] 日志行", len(bl)),
    ("opened（真开仓）", cnt(lambda l: "MidLongBrain] opened" in l)),
    ("skip open", cnt(lambda l: "skip open" in ln for ln in bl) if False else cnt(lambda l: "MidLongBrain] skip open" in l)),
    ("开仓扫描（候选/成交）", cnt(lambda l: "开仓扫描" in l and "候选=" in l)),
    ("出进程分析（子进程）", cnt(lambda l: "出进程分析" in l)),
    ("[MidLong] stage=fuse", cnt(lambda l: "stage=fuse" in l)),
    ("[MidLong] stage=exec", cnt(lambda l: "stage=exec" in l)),
])
print("      主脑 skip open 原因 Top8:", dict(skip.most_common(8)))

# ── 5. 跨系统：主脑 extras 载荷（现状） ──
try:
    from sqlalchemy import text
    from backend.database.connection import AnalyticsSessionLocal, SessionLocal
    db = AnalyticsSessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        print("\n【⑤ 台账：重启后的论题与否决（Analytics 库）】")
        for r in db.execute(text("""
            SELECT tier, direction, COUNT(*) FROM mlto_thesis
            WHERE created_at >= :rs GROUP BY 1,2 ORDER BY 3 DESC
        """), {"rs": RS or "1970-01-01"}).fetchall():
            print(f"   论题 tier={str(r[0]):6s} dir={str(r[1]):7s} n={r[2]}")
        for r in db.execute(text("""
            SELECT event_type, COUNT(*) FROM mlto_thesis_events
            WHERE ts >= :rs GROUP BY 1 ORDER BY 2 DESC
        """), {"rs": RS or "1970-01-01"}).fetchall():
            print(f"   事件 {str(r[0]):22s} n={r[1]}")
        rows = db.execute(text("""
            SELECT payload_json FROM mlto_thesis_events
            WHERE event_type='open_execute_false' AND ts >= :rs LIMIT 20
        """), {"rs": RS or "1970-01-01"}).fetchall()
        withr = sum(1 for (x,) in rows if '"reason"' in str(x))
        print(f"   open_execute_false 带 reason: {withr}/{len(rows)}")
    finally:
        db.close()
except Exception as exc:  # noqa: BLE001
    print(f"\n（台账读取失败: {type(exc).__name__}: {str(exc)[:110]}）")
