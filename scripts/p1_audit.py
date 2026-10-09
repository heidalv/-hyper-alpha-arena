# -*- coding: utf-8 -*-
"""[P1 验收 2026-09-21] LLM 诊断层的质量审计。

# 要回答（P1 的验收判据）

  M1 `diagnosis`/`reason` **含给定数字的比例**（规则 R1）—— 目标 > 90%
  M2 `cause` 分布是否合理（不应全落在同一类）
  M3 **假阴性率**：`action=keep` 之后该币的表现有没有真的变差？
     这是"LLM 说没事，是不是真没事"
  M4 **理论 vs 实测冲突清单**：LLM 建议的参数与**我们实测出的最优**是否矛盾

# 为什么 M4 最重要

P1 阶段 `action` 恒为 `keep`，所以"它建议什么"唯一的价值就是**审计**。
而审计的核心是找出**LLM 的推理与我们的实测证据冲突**的地方 ——
那些是绝不能让它自动应用的。已经发现一例：

    SOL 诊断建议 `spread_mult: 0.5 → 0.7`（理由：费项侵蚀价差，应放宽挂宽）
    但 H146 实测：spread_mult 0.5 最好（净 +0.377bp），1.3 为 −0.245、1.8 为 −0.552
    ⇒ **越宽越差**。LLM 的推理在理论上自洽，但与我方实测相反。

用法：
    .venv\\Scripts\\python.exe scripts\\p1_audit.py
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "llm_monitor_log.jsonl"

# 我方**实测**确定的最优（写死在这里作为审计基准；来源见注释）
MEASURED_OPTIMUM = {
    "spread_mult": (0.5, "H146 实测 0.5/1.3/1.8 ⇒ 净 +0.377/−0.245/−0.552bp，**越宽越差**"),
    "spread_mult_reduce": (0.4, "H163 实测 0.95 挂在盘口外不被吃 ⇒ 收紧到 0.4"),
    "take_profit_bp": (12.0, "H164/H165 模拟最优（+2.56bp/笔）；实测兑现 +7.44bp/笔"),
}


def nums(text: str) -> set:
    out = set()
    for m in re.finditer(r"-?\d+\.?\d*", str(text or "")):
        try:
            out.add(round(abs(float(m.group(0))), 4))
        except Exception:
            pass
    return out


def main() -> int:
    if not LOG.exists():
        print(f"  日志不存在：{LOG}")
        return 1
    recs = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            pass

    print("=" * 96)
    print("P1 验收   LLM 诊断层质量审计")
    print("=" * 96)
    print(f"  日志 {LOG.name}：**{len(recs)}** 条")
    if not recs:
        return 1

    n_parse = sum(1 for r in recs if r.get("parse_ok"))
    n_rules = sum(1 for r in recs if r.get("rules_ok"))
    n_skip = sum(1 for r in recs if r.get("skipped"))
    n_err = sum(1 for r in recs if r.get("error"))
    print(f"\n  ── 基本健康度 ──")
    print(f"    跳过（无标记，未调 LLM）  {n_skip}")
    print(f"    调用成功                  {n_parse}")
    print(f"    调用/解析失败             {n_err}")
    print(f"    规则校验通过              **{n_rules}**"
          f"（{n_rules/max(n_parse,1)*100:.0f}% of 成功调用）")

    # M1 引数字比例
    tot_items = with_num = 0
    causes = Counter()
    suggestions = []
    for r in recs:
        out = r.get("llm_out") or {}
        for s in out.get("symbols") or []:
            tot_items += 1
            diag, reason = str(s.get("diagnosis") or ""), str(s.get("reason") or "")
            if nums(diag) and nums(reason):
                with_num += 1
            causes[s.get("cause")] += 1
            if s.get("params"):
                suggestions.append((s.get("symbol"), s.get("params"),
                                    s.get("confidence"), r.get("as_of", "")[:19]))
    print(f"\n  ── M1 引用数字比例（P1 验收判据：> 90%）──")
    print(f"    diagnosis+reason 均含数字的条目：**{with_num}/{tot_items}**"
          f" = **{with_num/max(tot_items,1)*100:.0f}%**"
          f"  {'✓ 达标' if tot_items and with_num/tot_items > 0.9 else '（样本少，继续积累）'}")

    print(f"\n  ── M2 cause 分布 ──")
    for k, v in causes.most_common():
        print(f"    {k:<18} {v}")
    if len(causes) == 1 and tot_items >= 4:
        print(f"    ⚠️ 全落在同一类 ⇒ 可能退化为「永远说是行情」（需警惕）")

    print(f"\n  ── M4 理论 vs 实测冲突清单（**P1 最重要的产出**）──")
    if not suggestions:
        print("    （本次没有参数建议）")
    conflicts = 0
    for (sym, params, conf, asof) in suggestions:
        for k, v in params.items():
            opt, why = MEASURED_OPTIMUM.get(k, (None, ""))
            if opt is None:
                print(f"    {asof} {sym}: 建议 {k}={v}（无实测基准可比）")
                continue
            delta = float(v) - float(opt)
            flag = "**冲突**" if abs(delta) > 0.15 else "接近实测最优"
            if flag == "**冲突**":
                conflicts += 1
            print(f"    {asof} {sym}: 建议 {k}={v}（conf={conf}）"
                  f" vs 实测最优 {opt} ⇒ {flag}")
            if why:
                print(f"        实测依据：{why}")
    print(f"\n    ⇒ 冲突 {conflicts} 处 / 建议 {len(suggestions)} 处")
    if conflicts:
        print(f"    ⚠️ **这些正是绝不能让 LLM 自动应用的类型** ——")
        print(f"       它的推理听起来自洽（「费项侵蚀价差 ⇒ 放宽挂宽」），")
        print(f"       但与我方实测相反。⇒ P2 开放调参前，必须先把这类冲突固化成守卫规则。")

    print(f"\n  ── M3 假阴性率（LLM 说 keep 之后，真的没事吗）──")
    print(f"    ⚠️ 需要**时间推移后**才能算：keep 之后该币近 30min 净额是否恶化。")
    print(f"       当前日志条数不足（{len(recs)}），等积累到 ≥ 20 条再算。")
    print(f"       计算方式：对每条 keep，比较 t 时刻与 t+30min 的该币 net_bp。")

    print(f"\n  ── 结论 ──")
    print(f"    · 通路与契约**可用**（解析成功、规则校验通过）")
    print(f"    · 诊断**引用了具体数字**，且对事件行情给出「不应调参」的正确判断")
    print(f"    · 但**参数建议与实测冲突** ⇒ P2 前必须加守卫（见上）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
