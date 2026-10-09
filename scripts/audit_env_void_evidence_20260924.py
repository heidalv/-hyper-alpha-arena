# -*- coding: utf-8 -*-
"""系统性审计：`.env` 里**依据为 09-15 前样本**的配置（按用户「09-15 之前一律作废」规则）。

来由（R8）：`MIDLONG_SL_MAX_PCT_LONG` 的依据写的是 `[§88 执行 2026-09-11]`，
按作废规则失效；用 09-15+ 样本重验后放宽到 8%，两个样本都过预登记判据。
⇒ 这说明**可能还有其他键踩同一个坑**，需要系统扫一遍，而不是靠翻文档碰运气。

做法：
  1) 逐行解析 `.env`，把每个 `KEY=value` 与其**上方连续注释块**配对；
  2) 从注释块里抽日期（2026-08-xx / 2026-09-xx）与"轮N"标记；
  3) 判定：注释块里出现的日期**全部早于 09-15** ⇒ 标记为 **VOID**；
     完全没有日期 ⇒ 标 **NODATE**（无法判断，需人工看）；
     有 ≥09-15 的日期 ⇒ 标 OK；
  4) 只列与中长线入场/出场/仓位相关的键（SL/TP/TRAIL/HOLD/GATE/SIZE/RISK/COOLDOWN/BUDGET/
     NOTIONAL/LEVERAGE/CHOP/REGIME），并按"是否影响钱"粗排优先级。

输出：VOID / NODATE 清单，供逐条重验或补证据。
只读。
"""
from __future__ import annotations

import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ENV = ".env"
CUTOFF = (2026, 9, 15)
RELEVANT = re.compile(
    r"(SL_|_SL|TP_|_TP|TRAIL|HOLD|GATE|SIZE|RISK|COOLDOWN|BUDGET|NOTIONAL|LEVERAGE|CHOP|REGIME|"
    r"POSITION|MARGIN|STOP|LOCK|BREAKEVEN|STAGED|LOSS|PROFIT)", re.I)
SKIP_PREFIX = ("#",)
DATE = re.compile(r"20\d\d[-/](\d{2})[-/](\d{2})")


def dates_in(text: str):
    out = []
    for m in DATE.finditer(text):
        try:
            out.append((int(m.group(0)[:4]), int(m.group(1)), int(m.group(2))))
        except ValueError:
            continue
    return out


def main() -> int:
    with open(ENV, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    entries = []          # (key, value, lineno, comment_block)
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
    print("解析到 %d 个键（含注释块配对）" % len(entries))

    void, nodate, ok = [], [], []
    for key, val, ln, cb in entries:
        if not RELEVANT.search(key):
            continue
        text = "\n".join(cb)
        ds = dates_in(text)
        if not ds:
            nodate.append((key, val, ln, ""))
        elif all(d < CUTOFF for d in ds):
            void.append((key, val, ln, sorted({"%04d-%02d-%02d" % d for d in ds})[-1]))
        else:
            ok.append((key, val, ln, max("%04d-%02d-%02d" % d for d in ds)))
    print("相关键：VOID(仅 09-15 前证据) %d ｜ NODATE(无日期) %d ｜ OK(有 ≥09-15 证据) %d\n"
          % (len(void), len(nodate), len(ok)))

    print("=" * 104)
    print("【A】VOID —— 注释里出现的日期**全部早于 09-15**（按用户规则依据失效，建议逐条重验）")
    print("  %-42s %-14s %6s %s" % ("键", "值", "行号", "最新引证日期"))
    for key, val, ln, latest in sorted(void):
        print("  %-42s %-14s %6d %s" % (key, val[:14], ln, latest))

    print("\n" + "=" * 104)
    print("【B】NODATE —— 注释里没有任何日期（无法判断依据新旧，需人工看）")
    print("  %-42s %-14s %6s" % ("键", "值", "行号"))
    for key, val, ln, _ in sorted(nodate)[:60]:
        print("  %-42s %-14s %6d" % (key, val[:14], ln))
    if len(nodate) > 60:
        print("  …（共 %d 个，只列前 60）" % len(nodate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
