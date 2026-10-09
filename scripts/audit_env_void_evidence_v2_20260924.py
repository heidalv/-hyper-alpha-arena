# -*- coding: utf-8 -*-
"""作废证据扫描 **v2**：把"注释陈旧"与"代码里其实有新证据/根本没被读"分开。

v1（`audit_env_void_evidence_20260924.py`）只看 `.env` 注释块里的日期，
于是把 `MIDLONG_TREND_BROKEN_MIN_PRICE_LOSS` 误判为 VOID —— 它的 `.env` 注释引 09-11，
但**代码 docstring 里写着 2026-09-18 轮26 的证据**（trend_broken 24 笔实际均 −4.10、
政策反事实 +8.127%、胜率 83.3%）。R11 的三条候选全部属于这类"假 VOID"。

v2 对每个 VOID 键再做两步判定：
  A) **CODE?** 该键在 `backend/` 里到底有没有被读（一次都没出现 ⇒ 永不 bind，直接排除）；
  B) **CODE_FRESH?** 出现处 ±WINDOW 行内是否含 **≥2026-09-15** 的日期
     （有 ⇒ 代码侧已有新证据，属"注释陈旧"而非"依据失效"）。

分类：
  NO_CODE      代码里没有该键 → 永不生效，排除
  CODE_FRESH   代码侧有 ≥09-15 证据 → 假 VOID，排除
  CODE_STALE   代码在用、但代码侧也找不到 ≥09-15 证据 → **真候选，需重验**
只读。
"""
from __future__ import annotations

import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ENV = ".env"
CODE_ROOT = "backend"
CUTOFF = (2026, 9, 15)
WINDOW = 30
RELEVANT = re.compile(
    r"(SL_|_SL|TP_|_TP|TRAIL|HOLD|GATE|SIZE|RISK|COOLDOWN|BUDGET|NOTIONAL|LEVERAGE|CHOP|REGIME|"
    r"POSITION|MARGIN|STOP|LOCK|BREAKEVEN|STAGED|LOSS|PROFIT)", re.I)
DATE = re.compile(r"20\d\d[-/](\d{2})[-/](\d{2})")


def dates_in(text: str):
    out = []
    for m in DATE.finditer(text):
        out.append((int(m.group(0)[:4]), int(m.group(1)), int(m.group(2))))
    return out


def load_code_lines():
    files = []
    for dirpath, _dirs, names in os.walk(CODE_ROOT):
        for n in names:
            if n.endswith(".py"):
                p = os.path.join(dirpath, n)
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        files.append((p, f.readlines()))
                except OSError:
                    continue
    return files


def main() -> int:
    with open(ENV, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    entries = []
    block: list = []
    for i, raw in enumerate(lines, 1):
        s = raw.rstrip("\n")
        if s.strip().startswith("#") or not s.strip():
            block.append(s)
            continue
        m = re.match(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", s)
        if m:
            entries.append((m.group(1), m.group(2).strip(), i, list(block)))
        block = []

    void = []
    for key, val, ln, cb in entries:
        if not RELEVANT.search(key):
            continue
        ds = dates_in("\n".join(cb))
        if ds and all(d < CUTOFF for d in ds):
            void.append((key, val, ln))
    print("VOID 候选（仅 .env 注释日期 < 09-15）：%d 个" % len(void))
    print("正在做代码侧判定（窗口 ±%d 行，扫描 %s/）…\n" % (WINDOW, CODE_ROOT))
    code = load_code_lines()

    buckets = {"NO_CODE": [], "CODE_FRESH": [], "CODE_STALE": []}
    by_path = {p: cl for p, cl in code}
    for key, val, ln in void:
        hits = []          # (path, idx)
        for path, cl in code:
            for i, line in enumerate(cl):
                if key in line:
                    hits.append((path, i))
        if not hits:
            buckets["NO_CODE"].append((key, val, ln, 0, ""))
            continue
        fresh = ""
        # [R11 二次修 bug] 两个坑：
        #  ① 旧写法在第二个循环里用 `cl`，那是**上一个循环残留的最后一个文件**；
        #  ② 用"±30 行窗口"找日期会把**邻近键**的注释算进来 —— 实测 `env_registry.py` 里
        #     `MIDLONG_SL_MAX_PCT_LONG` 上方就是 `[§88 2026-09-11]`（陈旧），
        #     但紧邻的 **mid** 键注释是 `[调研轮15b 2026-09-16]` ⇒ 被误判为 CODE_FRESH。
        #     （这正是 R8 真候选被漏掉的原因，属假阴性。）
        # 现改为：只看**同一行 + 紧邻上方的连续注释块**（该键自己的说明）。
        for path, i in hits:
            cl = by_path.get(path) or []
            seg_lines = [cl[i]] if i < len(cl) else []
            j = i - 1
            while j >= 0 and cl[j].lstrip().startswith("#"):
                seg_lines.append(cl[j])
                j -= 1
            for d in dates_in("\n".join(seg_lines)):
                if d >= CUTOFF:
                    fresh = "%04d-%02d-%02d@%s" % (d[0], d[1], d[2], os.path.basename(path))
                    break
            if fresh:
                break
        if fresh:
            buckets["CODE_FRESH"].append((key, val, ln, len(hits), fresh))
        else:
            buckets["CODE_STALE"].append((key, val, ln, len(hits), ""))

    for name, desc in (("NO_CODE", "代码里根本没读 ⇒ 永不生效，排除"),
                       ("CODE_FRESH", "代码侧已有 ≥09-15 证据 ⇒ 假 VOID，排除"),
                       ("CODE_STALE", "代码在用、但代码侧也无 ≥09-15 证据 ⇒ **真候选**")):
        rows = buckets[name]
        print("=" * 100)
        print("【%s】%d 个 —— %s" % (name, len(rows), desc))
        if rows:
            print("  %-44s %-12s %5s %6s %s" % ("键", "值", "行号", "代码引用", "代码侧最新证据"))
            for key, val, ln, n, fresh in sorted(rows):
                print("  %-44s %-12s %5d %6d %s" % (key, val[:12], ln, n, fresh or "-"))
        print()
    print("⇒ 真候选（CODE_STALE）共 %d 个，按影响面人工排序后逐条重验。" % len(buckets["CODE_STALE"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
