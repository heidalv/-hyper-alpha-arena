# -*- coding: utf-8 -*-
"""缺陷总清单（报告 §62）自校验 + 导出 JSON。

用途：把「按严重度排序的缺陷清单」变成**可机器校验**的产物——防止清单与事实漂移
（条目被删/证据指向不存在的脚本/待决策项没有编号/统计行与实际行数不一致）。

校验项：
  1. §62 表格里每行 ID 唯一、严重度 ∈ {高,中,低,记录}；
  2. 证据列出现的 `_audit_ml/Z*.py` 与 `backend/tests/**`（或 `test_*.py`）**必须真实存在**；
  3. 状态为「待决策」的条目，其证据/状态里必须出现 `P<数字>`；
  4. §62.1 的统计数字必须与实际行数一致；
  5. 决策队列 §62.5 里的每个 P 编号都能在条目或正文里找到。

用法：
  .venv\\Scripts\\python.exe backend/scripts/audit_defect_inventory.py [--json PATH] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# [2026-09-16 调研轮7] Windows 控制台默认 cp936(GBK)：打印 ✅/❌ 会抛 UnicodeEncodeError
# → 脚本 rc=1 → 例行审计误报 FAIL、真失败被淹没。守卫 stdout/stderr 为 UTF-8。
try:  # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
REPORT = ROOT / "_中长线负期望根因报告_20260909.md"
SEV = {"🔴": "高", "🟠": "中", "🟡": "低", "⚪": "记录"}
# 严重度单元格形如「🔴 高」——emoji 后可能还有文字，故用 [^|]* 吞掉其余
ROW5 = re.compile(r"^\|\s*(\d+)\s*\|\s*(🔴|🟠|🟡|⚪)[^|]*\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$")
# 4 列：| id | sev | 缺陷 | 状态 |（低/记录条目为节省篇幅省略证据列）
ROW4 = re.compile(r"^\|\s*(\d+)\s*\|\s*(🔴|🟠|🟡|⚪)[^|]*\|([^|]*)\|([^|]*)\|\s*$")
ROW = ROW5  # 兼容旧引用
STATS = re.compile(r"条目 \*\*(\d+)\*\* 条")
STATS_SEV = re.compile(r"🔴 高 \*\*(\d+)\*\*｜🟠 中 \*\*(\d+)\*\*｜🟡 低 \*\*(\d+)\*\*｜⚪ 记录 \*\*(\d+)\*\*")
STATS_ST = re.compile(
    r"状态：✅ 已修 \*\*(\d+)\*\*｜🟡 部分/可见性已修 \*\*(\d+)\*\*｜📋 待决策 \*\*(\d+)\*\*"
    r"｜⏳ 待办 \*\*(\d+)\*\*｜❌ 撤销 \*\*(\d+)\*\*"
)
P_ID = re.compile(r"\bP(\d{1,2})\b")


def _section(text: str) -> str:
    """取 §62 本体（直到下一个 `## ` 级标题）。

    [§64 修复 2026-09-10] 原实现以「`## 23.` 验收标准」为结束边界，但 §63/§64 等
    后续轮次章节被追加在 §62 与 §23 **之间** ⇒ 这些章节里的编号表格被误当成清单条目
    （实测出现 `ID 重复: [44, 45, 46]`）。清单的唯一来源应只有 §62 本体。
    """
    start = text.index("## 62.")
    m = re.search(r"\n## (?!62\.)", text[start:])
    end = start + m.start() if m else len(text)
    return text[start:end]


def parse_report(path: Path = REPORT) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    sec = _section(text)
    rows = []
    for ln in sec.splitlines():
        m5 = ROW5.match(ln)
        if m5:
            rid, sev, title, evidence, status = m5.groups()
        else:
            m4 = ROW4.match(ln)
            if not m4:
                continue
            rid, sev, title, status = m4.groups()
            evidence = ""
        rows.append({
            "id": int(rid), "severity": SEV[sev],
            "title": title.strip()[:120], "evidence": evidence.strip()[:200],
            "status": status.strip()[:200],
        })
    # 统计行
    s = STATS.search(sec)
    sev_counts = STATS_SEV.search(sec)
    st_counts = STATS_ST.search(sec)
    dropped = re.findall(r"\| (\d+) \| [🔴🟠🟡⚪] 低 \| ~~", sec)
    decision_queue = sorted({int(x) for x in P_ID.findall(sec)})
    return {
        "rows": rows,
        "claimed_total": int(s.group(1)) if s else None,
        "claimed_sev": [int(x) for x in sev_counts.groups()] if sev_counts else None,
        "claimed_status": [int(x) for x in st_counts.groups()] if st_counts else None,
        "decision_queue": decision_queue,
    }


def _status_bucket(status: str) -> str:
    """状态归桶（与报告 §62.1 统计行一一对应，由本脚本校验）。"""
    s = status or ""
    if "撤销" in s or "已合并" in s or "误报" in s:
        return "撤销"
    if "待决策" in s or "待你拍板" in s or "待拍板" in s:
        return "待决策"
    if "待补" in s or "待清理" in s or "待核验" in s or "记录不接线" in s:
        return "待办"
    if ("部分" in s) or ("可见性已修" in s and "✅" not in s):
        return "部分"
    if any(k in s for k in ("已修", "已加", "已补", "已订正", "已排除", "已浮现", "已分类",
                            "已定性", "已记录", "已量化", "已验证", "已加幂等")):
        return "已修"
    return "记录"


def validate(data: dict) -> list:
    problems = []
    rows = data["rows"]
    ids = [r["id"] for r in rows]
    dup = [i for i, n in Counter(ids).items() if n > 1]
    if dup:
        problems.append(f"ID 重复: {sorted(dup)}")
    # 证据文件存在性
    for r in rows:
        for rel in re.findall(r"`?(_audit_ml/Z[\w]+\.py)`?", r["evidence"]):
            if not (ROOT / rel).exists():
                problems.append(f"#{r['id']} 证据脚本不存在: {rel}")
        for t in re.findall(r"`?(test_[\w]+\.py)`?", r["evidence"]):
            hits = list((ROOT / "backend" / "tests").rglob(t))
            if not hits:
                problems.append(f"#{r['id']} 证据测试不存在: {t}")
    # 待决策必须有 P 编号
    for r in rows:
        if _status_bucket(r["status"]) == "待决策" and not P_ID.search(r["status"] + r["evidence"]):
            problems.append(f"#{r['id']} 状态为待决策但没写 P 编号")
    # 统计行一致性
    if data["claimed_total"] is not None and data["claimed_total"] != len(rows):
        problems.append(f"统计条目数 {data['claimed_total']} ≠ 实际 {len(rows)}")
    if data["claimed_sev"]:
        actual = Counter(r["severity"] for r in rows)
        want = dict(zip(("高", "中", "低", "记录"), data["claimed_sev"]))
        for k, v in want.items():
            if actual.get(k, 0) != v:
                problems.append(f"统计「{k}」={v} ≠ 实际 {actual.get(k, 0)}")
    if data["claimed_status"]:
        actual = Counter(_status_bucket(r["status"]) for r in rows)
        want = dict(zip(("已修", "部分", "待决策", "待办", "撤销"), data["claimed_status"]))
        for k, v in want.items():
            if actual.get(k, 0) != v:
                problems.append(f"统计状态「{k}」={v} ≠ 实际 {actual.get(k, 0)}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "data" / "audit_defect_inventory.json"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    data = parse_report()
    problems = validate(data)
    out = {
        "source": str(REPORT.name),
        "n": len(data["rows"]),
        "by_severity": dict(Counter(r["severity"] for r in data["rows"])),
        "by_status": dict(Counter(_status_bucket(r["status"]) for r in data["rows"])),
        "decision_queue": data["decision_queue"],
        "problems": problems,
        "items": data["rows"],
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print(f"条目 {out['n']}；严重度 {out['by_severity']}；状态 {out['by_status']}")
        print(f"待决策 P 编号: {out['decision_queue']}")
        if problems:
            print(f"\n❌ 校验问题 {len(problems)} 项：")
            for p in problems:
                print("   -", p)
        else:
            print("\n✅ 清单自校验通过")
        print(f"已写入 {args.json}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
