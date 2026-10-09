# -*- coding: utf-8 -*-
"""[F364b 2026-09-18] 六批声明查证 · **精确第二轮**（首轮口径太粗，逐条纠偏）。

首轮教训（写进纪律）：粗粒度"文件里有这个词"会产生两类错误——
- **假通过**：C6/C7 我匹配到的是任意含"冷却/ensure_bound_strategy"的行，不是该修复本身；
- **假失败**：C2 我用 `len(dict)` 数条目 ⇒ 报"2 条"，其实文件是 `{_meta, formulas}` 结构，
  真实条数要看 `formulas`；C4 的"缺表"也可能是**改名**而非缺失。
本轮全部改为**定位到具体实现点/具体字段**。
"""
from __future__ import annotations

import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def sec(t: str) -> None:
    print("\n" + "=" * 84)
    print(t)
    print("=" * 84)


def main() -> int:
    # ── C1：adaptive_executor 是包？导入能否解析？ ──
    sec("C1 §6『待修』：from adaptive_executor import get_stop_manager 能否解析")
    pkg = ROOT / "backend/services/adaptive_executor"
    print(f"  adaptive_executor 是包 = {pkg.is_dir()}（__init__.py 存在={ (pkg / '__init__.py').exists() }）")
    init = pkg / "__init__.py"
    if init.exists():
        t = init.read_text(encoding="utf-8", errors="replace")
        reexport = [ln.strip() for ln in t.splitlines()
                    if "get_stop_manager" in ln or "get_position_sizer" in ln]
        print(f"  __init__ 中对两个名字的再导出: {reexport or '（无）'}")
    sites = []
    for f in (ROOT / "backend").rglob("*.py"):
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, ln in enumerate(t.splitlines(), 1):
            if re.search(r"^\s*from\s+adaptive_executor\s+import|^\s*from\s+backend\.services\.adaptive_executor\s+import", ln):
                sites.append(f"{f.relative_to(ROOT).as_posix()}:{i} {ln.strip()[:96]}")
    print("  包根导入点:")
    for s in sites:
        print(f"    {s}")
    import importlib
    try:
        m = importlib.import_module("backend.services.adaptive_executor")
        ok = hasattr(m, "get_stop_manager")
        print(f"  运行时: import backend.services.adaptive_executor 成功；"
              f"hasattr(get_stop_manager) = {ok}")
    except Exception as exc:
        print(f"  运行时导入失败: {type(exc).__name__}: {str(exc)[:120]}")

    # ── C2：GTJA 子集真实条数 + docstring 漂移 ──
    sec("C2 §7.2 GTJA191 子集：真实条数与文档一致性")
    p = ROOT / "backend/data/factors_lab/gtja191_subset.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    forms = d.get("formulas") if isinstance(d, dict) else d
    meta = d.get("_meta") if isinstance(d, dict) else {}
    print(f"  _meta.name = {meta.get('name')!r}")
    print(f"  真实 formula 条数 = {len(forms)}")
    print(f"  示例 id: {[f.get('id') for f in forms[:6]]}")
    cal = (ROOT / "backend/services/factors_lab/calibration.py").read_text(encoding="utf-8")
    doc = re.search(r'"""\[AR-3[^"]*"""', cal)
    print(f"  calibration.py docstring: {doc.group(0)[:80] if doc else '（未匹配）'}")
    if doc and "10条" in doc.group(0) and len(forms) != 10:
        print(f"  ⇒ **文档漂移**：docstring 写 10 条，实际 {len(forms)} 条（_meta 自称 "
              f"{meta.get('name')}）")

    # ── C4：4 张战略表：缺表还是改名？ ──
    sec("C4 §8 B2 战略表：缺表 vs 改名")
    tnames = {}
    for f in (ROOT / "backend/database").rglob("*.py"):
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in re.finditer(r"__tablename__\s*=\s*[\"']([^\"']+)[\"']", t):
            tnames[m.group(1)] = f.name
    want = ["strategic_reports", "macro_snapshots", "memories", "new_coin_opportunities"]
    for w in want:
        print(f"  模型层 __tablename__='{w}' 出现于: {tnames.get(w, '（无此模型）')}")
    try:
        from sqlalchemy import text
        from backend.database.connection import AnalyticsSessionLocal as Sess
        db = Sess()
        try:
            rows = db.execute(text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                "AND (table_name LIKE '%macro%' OR table_name LIKE '%memor%' "
                "OR table_name LIKE '%strateg%' OR table_name LIKE '%opportun%') "
                "ORDER BY table_name")).fetchall()
            print("  alpha_analytics 中相关表:")
            for (t,) in rows:
                try:
                    n = db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                except Exception:
                    n = "?"
                print(f"    {t:<34} {n} 行")
        finally:
            db.close()
    except Exception as exc:
        print("  查询失败:", str(exc)[:140])

    # ── C6：同因拦截冷却的具体常量与实现 ──
    sec("C6 §10.1 同因拦截冷却：常量与实现点")
    svc = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8")
    i = svc.find("_BLOCK_STREAK_FILE")
    if i < 0:
        print("  未找到 _BLOCK_STREAK_FILE")
    else:
        blk = svc[max(0, i - 1500): i + 3200]
        for kw in ("STREAK", "COOLDOWN", "_BLOCK_STREAK_FILE", "streak", "cooldown"):
            hits = [ln.strip()[:100] for ln in blk.splitlines()
                    if kw in ln and ("=" in ln or "def " in ln)]
            if hits:
                print(f"  [{kw}]")
                for h in hits[:5]:
                    print(f"    {h}")
    print(f"  streak 文件存在 = {(ROOT / 'data/proposal_block_streaks.json').exists()}")

    # ── C7：strategy_detached 补建逻辑 + 现象是否仍在发生 ──
    sec("C7 §11 strategy_detached：补建逻辑与现象时间线")
    pe = (ROOT / "backend/services/full_auto/proposal_execution.py").read_text(encoding="utf-8")
    hits = [(k, ln.strip()[:100]) for k, ln in enumerate(pe.splitlines(), 1)
            if re.search(r"auto_create_strategy|补建|detached|ensure_bound_strategy", ln)]
    print(f"  proposal_execution.py 相关行 {len(hits)} 条:")
    for k, ln in hits[:12]:
        print(f"    :{k} {ln}")
    audit = ROOT / "data/midlong_direction_audit.jsonl"
    if audit.exists():
        ts = []
        for ln in audit.read_text(encoding="utf-8", errors="replace").splitlines():
            if "strategy_detached" not in ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            t = r.get("ts") or r.get("time") or r.get("created_at")
            if t:
                ts.append(str(t))
        print(f"  审计文件中 strategy_detached 共 {len(ts)} 条")
        if ts:
            print(f"    最早 = {min(ts)}")
            print(f"    最晚 = {max(ts)}")
            print(f"    （现在 = {datetime.now(timezone.utc).isoformat()}）")
            print("    ⇒ 若最晚时间晚于 §11 修复日（2026-09-18），说明现象仍在发生")
    else:
        print("  审计文件不存在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
