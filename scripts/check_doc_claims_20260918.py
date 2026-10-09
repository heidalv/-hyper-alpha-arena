"""声明自检：把三份文档里的"计数类声明"逐条对回文件系统事实。

动机（纪律 25/26）：文档写"30 项 / 183 条 / 71 个文件"，一旦脚本或目录变了，
文档就会静默漂移成假数字。本脚本让漂移**当场可见**（只读，不改任何东西）。

用法：backend\\.venv\\Scripts\\python.exe scripts\\check_doc_claims_20260918.py
"""
from __future__ import annotations

import io
import os
import re
import sys

# 与本仓库既有脚本同款处理：Windows 控制台默认 GBK，打印 "⇒" 之类字符会直接崩，
# 而这正是"读数工具本身要验收"（纪律 25）里最容易再次踩到的一类坑。
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REP = "docs/复查报告_因子×LLM架构_20260917.md"
TODO = "docs/复查后待办_可执行清单_20260918.md"
SUMM = "docs/复查总结_给决策者_20260918.md"
TESTDIR = "backend/tests/unit"
ARCH = "logs/review_snapshot_20260917/changed_by_review"

rows: list[tuple[str, str, str, bool]] = []


def check(name: str, claimed: str, actual: str) -> None:
    rows.append((name, claimed, actual, claimed == actual))


def main() -> int:
    r = open(REP, encoding="utf-8").read()
    t = open(TODO, encoding="utf-8").read()
    s = open(SUMM, encoding="utf-8").read()
    v = open("scripts/verify_review_findings.py", encoding="utf-8").read()

    # 1) 报告章数（## 级）
    check("报告 ## 章数", "41", str(len(re.findall(r"(?m)^## ", r))))
    # 2) 待办区间（A 项是表格行 `| A1 | …`，B 项是 `### B1.` 小标题 —— 两种格式都要按实际来）
    bids = sorted({int(m.group(1)) for m in re.finditer(r"(?m)^### B(\d+)\.", t)})
    aids = sorted({int(m.group(1)) for m in re.finditer(r"(?m)^\| A(\d+) \|", t)})
    check("待办 A 区间", "A1-A6",
          f"A{aids[0]}-A{aids[-1]}" if aids else "无（正则未匹配）")
    check("待办 B 区间", "B1-B29", f"B{bids[0]}-B{bids[-1]}" if bids else "无")
    # 3) 复验器项数（RESULTS 由 @check 装饰器登记，不是字面量元组）
    nres = len(re.findall(r"(?m)^@check\(", v))
    check("复验器项数", "33", str(nres))
    # 4) 回归锁文件数
    files = [f for f in os.listdir(TESTDIR) if f.endswith("_20260918.py")]
    check("回归锁文件数", "32", str(len(files)))
    # 5) 归档载荷文件数（不含 _hashes.txt 清单）
    files_arch = [f for f in os.listdir(ARCH) if f != "_hashes.txt"]
    check("归档载荷文件数", "71", str(len(files_arch)))
    man = sum(1 for _ in open(os.path.join(ARCH, "_hashes.txt"), encoding="utf-8"))
    check("_hashes.txt 行数", "142", str(man))
    # 6) 交叉：一页纸是否声称了与上列相同的数
    for frag, label in [
        ("41 个", "一页纸声明 41 章"),
        ("A1–A6 + B1–B29", "一页纸声明待办区间"),
        ("33 项，当前 33/33 PASS", "一页纸声明 33/33"),
        ("32 个测试文件", "一页纸声明 32 文件"),
        ("71 个文件", "一页纸声明 71 文件"),
    ]:
        check(label, "在", "在" if frag in s else "缺失")

    bad = 0
    print("=" * 74)
    print("声明自检（文档计数 vs 文件系统事实）")
    print("=" * 74)
    for name, claimed, actual, ok in rows:
        mark = "OK  " if ok else "DRIFT"
        if not ok:
            bad += 1
        print(f"  [{mark}] {name:<22} 声明={claimed:<12} 实际={actual}")
    print("-" * 74)
    print(f"合计 {len(rows)} 项：一致 {len(rows) - bad} / 漂移 {bad}")
    if bad:
        print("**文档与事实漂移** -> 请同步三份文档（本脚本刻意不自动改）。")
    print("\n刻意不校验（无法离线从文件事实推出，必须实跑才有的读数）：")
    print("  1. 用例条数与 passed/skipped —— 需实跑 pytest：")
    print("     python -m pytest <19 个 *_20260918.py> -q")
    print("  2. 复验器 PASS 数 —— 需实跑 scripts/verify_review_findings.py（当前 30/30）。")
    print("  3. 任何运行态读数（prompt 字符数、载荷占比、DB 行数）。")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
