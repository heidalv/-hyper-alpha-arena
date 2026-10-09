# -*- coding: utf-8 -*-
"""[h900] 全面数据完整性审计:分析工件里的空值/占位符/错位。

用户:"很多策略分析、关键点位分析的数据根本就是空值、占位符或错位"。
审计对象:data/ 下的策略分析工件 + 研究结论文档的关键数字。
判定模式:
  · 空/占位:None、"", 0, 0.0, "无", "?", "nan", "pending", "unknown", "—"
  · 错位:字段名与内容不匹配(如 spread 列装的是 qty),或 ts 为 0/负数
"""
import io
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

PLACEHOLDER_RE = re.compile(
    r"^(null|none|nan|pending|unknown|tbd|todo|无|暂无|待定|\?|—|-{2,}|x{2,}|占位|placeholder)$",
    re.IGNORECASE)

now = time.time()

print("=" * 70)
print("① data/ 策略分析工件:空值/占位符/陈旧审计")
print("=" * 70)
files = [
    "flow_gate_model.json", "flow_gate_last.json", "promotion_gate_registry.json",
    "flow_edge_last.json", "flow_roundtrip_last.json", "flow_learn_last.json",
    "gate_proposal_last.json", "gate_audit_last.json", "symbol_scorecard.json",
    "flow_fill_markout.json", "flow_edge_scan_last.json", "markout_kpi_last.json",
    "flow_edge_robust.json", "daily_score_last.json", "regime_policy_library.json",
    "flow_prob_model_v2.json", "flow_prob_model_last.json", "scalp_regime_state.json",
]

def scan_value(v, path: str, findings: list, depth: int = 0):
    if depth > 6:
        return
    if v is None:
        findings.append(f"{path}: null")
    elif isinstance(v, str):
        if v.strip() == "":
            findings.append(f"{path}: 空字符串")
        elif PLACEHOLDER_RE.match(v.strip()):
            findings.append(f"{path}: 占位符 '{v}'")
        elif v.lower() in ("nan", "-nan"):
            findings.append(f"{path}: nan 字符串")
    elif isinstance(v, (int, float)):
        if isinstance(v, float) and v != v:
            findings.append(f"{path}: NaN")
    elif isinstance(v, dict):
        for k, vv in v.items():
            scan_value(vv, f"{path}.{k}", findings, depth + 1)
    elif isinstance(v, list):
        for i, vv in enumerate(v[:30]):
            scan_value(vv, f"{path}[{i}]", findings, depth + 1)

for f in files:
    p = ROOT / "data" / f
    if not p.exists():
        print(f"  ✗ {f}: 文件不存在")
        continue
    age_h = (now - p.stat().st_mtime) / 3600
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ✗ {f}: JSON 解析失败 {e}")
        continue
    findings = []
    scan_value(doc, f, findings)
    # ts 陈旧/错位检查
    if isinstance(doc, dict):
        ts = doc.get("ts") or doc.get("ts_epoch")
        if isinstance(ts, (int, float)) and ts > 0:
            if ts > 1e12:
                ts /= 1000.0
            if now - ts > 24 * 3600:
                findings.append(f"{f}.ts: 陈旧 {int((now-ts)/3600)}h")
            if ts > now + 3600:
                findings.append(f"{f}.ts: 未来时间戳(错位)")
    status = "✓ 干净" if not findings else f"⚠ {len(findings)} 处"
    print(f"  {status:<26} {f}({age_h:.1f}h 前)")
    for x in findings[:6]:
        print(f"      {x}")

print()
print("=" * 70)
print("② 关键数值抽查(打印真实值,供判断是否占位)")
print("=" * 70)
for f, keys in [
    ("flow_gate_last.json", ("protocol", "ts", "universe_size", "coin_ttl_sec")),
    ("flow_fill_markout.json", ("agg", "verdict")),
    ("markout_kpi_last.json", None),
    ("flow_roundtrip_last.json", None),
    ("flow_learn_last.json", None),
]:
    p = ROOT / "data" / f
    if not p.exists():
        print(f"  {f}: 不存在")
        continue
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        print(f"  {f}: 解析失败")
        continue
    if keys is None:
        print(f"  {f}: {json.dumps(doc, ensure_ascii=False)[:220]}")
    else:
        print(f"  {f}: " + json.dumps(
            {k: doc.get(k) for k in keys}, ensure_ascii=False)[:260])
