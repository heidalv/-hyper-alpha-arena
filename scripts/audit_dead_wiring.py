# -*- coding: utf-8 -*-
"""断线审计 v2（纯 Python 扫描，不依赖 rg）：参数 / 风控字段 / 状态标记 的接线状态。

分类：
  DEAD  = 除"定义处"外，backend 全树 0 次出现（完全没接线）
  WEAK  = 只出现在序列化/遥测/导出/脚本，决策路径（runner.py plan_tick/core）不读
  OK    = 在 runner.py 或 core.py 的决策路径被读取
只读。
"""
from __future__ import annotations

import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND = ROOT / "backend"
CORE = BACKEND / "services" / "market_maker" / "core.py"
RUNNER = BACKEND / "services" / "market_maker" / "runner.py"
DECISION_FILES = {"runner.py", "core.py", "replay.py"}

PY_FILES = [p for p in BACKEND.rglob("*.py")
            if "__pycache__" not in str(p) and "tests" not in str(p)]


def _dataclass_fields(path: pathlib.Path, cls: str) -> list[str]:
    src = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^class {cls}\b", src, re.M)
    if not m:
        return []
    tail = src[m.end():]
    nxt = re.search(r"^(class |def |@)", tail, re.M)
    body = tail[:nxt.start()] if nxt else tail
    return re.findall(r"^    (\w+)\s*:\s*[^=\n]+=", body, re.M)


FIELD_RE = re.compile(r"\b(\w+)\b")


def scan(names: list[str]) -> dict:
    """单遍扫描：每个文件只做一次分词，再按名字查表（避免 N×M 次正则）。"""
    from collections import Counter
    hits = {n: {"files": set(), "count": 0} for n in names}
    nameset = set(names)
    for p in PY_FILES:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        cnt = Counter(w for w in re.findall(r"[A-Za-z_]\w*", txt) if w in nameset)
        for n, k in cnt.items():
            hits[n]["count"] += k
            hits[n]["files"].add(p.name)
    return hits


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from scripts.h422_weekly_scan import read_env_dsn
    import psycopg

    c = psycopg.connect(read_env_dsn(), autocommit=True)
    cur = c.cursor()
    cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
    m = cur.fetchone()[0]
    params = m.get("params") or {}

    lim_fields = _dataclass_fields(CORE, "LaneRiskLimits")
    qp_fields = _dataclass_fields(CORE, "QuoteParams")
    names = sorted(set(lim_fields) | set(qp_fields) | set(params.keys()))
    hits = scan(names)

    dead, weak, ok = [], [], []
    for n in names:
        h = hits[n]
        files = h["files"]
        decision = files & DECISION_FILES
        if h["count"] <= 1:
            dead.append((n, params.get(n), h["count"], sorted(files)))
        elif not decision:
            weak.append((n, params.get(n), h["count"], sorted(files)))
        else:
            ok.append(n)

    print(f"== 参数接线审计 v2（{len(names)} 项：LaneRiskLimits {len(lim_fields)} + "
          f"QuoteParams {len(qp_fields)} + registry {len(params)} 去重）==\n")
    print(f"-- DEAD（除定义外 0 引用）: {len(dead)} --")
    for n, v, cnt, files in dead:
        print(f"  {n:<26} 现值={str(v):<10} 引用={cnt} {files}")
    print(f"\n-- WEAK（有引用但决策路径不读）: {len(weak)} --")
    for n, v, cnt, files in weak:
        print(f"  {n:<26} 现值={str(v):<10} 引用={cnt} 文件={files[:5]}")
    print(f"\n-- OK（runner/core 决策路径读取）: {len(ok)} --")
    print("  " + ", ".join(sorted(ok)))

    # 状态标记：runner 里 `state.X =` 写入但无读取
    rsrc = RUNNER.read_text(encoding="utf-8", errors="replace")
    writes = set(re.findall(r"\bstate\.(\w+)\s*=", rsrc)) | \
        set(re.findall(r"\bst\.(\w+)\s*=", rsrc)) | \
        set(re.findall(r"\bself\.(\w+)\s*=", rsrc))
    print(f"\n== 状态标记只写不读审计（{len(writes)} 个写入点）==")
    sus = []
    for w in sorted(writes):
        reads = len(re.findall(rf"\b(?:state|st|self|s)\.{re.escape(w)}\b(?!\s*=)", rsrc))
        reads += len(re.findall(rf"getattr\([^)]*['\"]{re.escape(w)}['\"]", rsrc))
        if reads == 0 and len(w) > 3:
            sus.append(w)
    for w in sus:
        print(f"  ⚠ {w}（仅写入，runner 内无读取；可能是遥测或断线）")
    if not sus:
        print("  （无）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
