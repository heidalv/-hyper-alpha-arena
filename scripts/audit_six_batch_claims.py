# -*- coding: utf-8 -*-
"""[F364 2026-09-18] 六批实施记录里**此前未核验**的声明 —— 逐条查证。

前几轮复查覆盖了 U1 四条、流A/B/C、B4 归因、学习读回路、评分与对拍；
但设计文档 §6–§11 里还有 6 条**从未被独立核验**的声明。本脚本把它们一次性查清，
避免"复查报告看起来很长，其实漏了整块"。

| 编号 | 出处 | 声明 |
|---|---|---|
| C1 | §6 新发现 | `ai_decision_integration.build_execution_context` 内 `from adaptive_executor import get_stop_manager` **导入名不存在**（记录待修） |
| C2 | §7.2 | GTJA 191 校准子集 10 条 → `backend/data/factors_lab/gtja191_subset.json`，校准集 102→112 全绿 |
| C3 | §7.3 | c₁/c₂ 语义对齐打分（`FACTORS_LAB_ALIGN_MIN`，c1·c2<0.35 拒收 align_fail） |
| C4 | §8 B2 | 恢复 `macro_snapshot_id` + create_all 建 4 张战略表 |
| C5 | §8 B3 | `.env` `KLINE_LLM_MAX_PER_CYCLE` 4→16 |
| C6 | §10.1 | `proposal_execution` 连续 5 次同因拦截 → 30 分钟冷却（`data/proposal_block_streaks.json`） |
| C7 | §11 | `strategy_detached` 根治：strat 缺位时自动补建一次再解析 |

只读；不写库、不改文件、不重启。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

RESULTS = []


def rec(cid: str, title: str, verdict: str, detail: str) -> None:
    RESULTS.append((cid, title, verdict, detail))
    print(f"\n[{verdict}] {cid} {title}")
    for ln in str(detail).splitlines():
        print(f"    {ln}")


def _read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _grep(rel: str, pat: str, flags=0):
    txt = _read(rel)
    return [(i + 1, ln.strip()) for i, ln in enumerate(txt.splitlines())
            if re.search(pat, ln, flags)]


# ── C1：§6 记录待修的导入 ──
def c1() -> None:
    ae = ROOT / "backend/services/adaptive_executor.py"
    hits = _grep("backend/services/adaptive_executor.py", r"def get_stop_manager|^def get_position_sizer|class .*StopManager")
    import_sites = _grep("backend/services/ai_decision_integration.py", r"from adaptive_executor import|adaptive_executor import")
    pkg_root = _grep("backend/services/adaptive_executor.py", r"^def |^class ", 0)
    if not ae.exists():
        rec("C1", "§6 待修导入", "无法判定", "adaptive_executor.py 不存在")
        return
    detail = [f"adaptive_executor 顶层定义（前 8 个）: {[h[1][:48] for h in pkg_root[:8]]}",
              f"ai_decision_integration 里的相关 import: {[h[1][:80] for h in import_sites]}"]
    has_top = any(re.match(r"def get_stop_manager", h[1]) for h in hits)
    still_import = [h for h in import_sites if "get_stop_manager" in h[1]]
    if still_import and not has_top:
        detail.append("⇒ **仍未整改**：仍在从包根导入 get_stop_manager，而该名字不在模块顶层")
        rec("C1", "§6 待修导入", "仍存在", "\n".join(detail))
    elif still_import and has_top:
        detail.append("⇒ 已可在顶层解析（导入不再失败）")
        rec("C1", "§6 待修导入", "已不成立", "\n".join(detail))
    else:
        detail.append("⇒ 调用点已不再使用该导入形式")
        rec("C1", "§6 待修导入", "已消除", "\n".join(detail))


# ── C2：GTJA 子集 ──
def c2() -> None:
    p = ROOT / "backend/data/factors_lab/gtja191_subset.json"
    refs = _grep("backend/services/factors_lab/agent3_expression.py", r"gtja") + \
        _grep("backend/services/factors_lab/calibration", r"gtja") if (ROOT / "backend/services/factors_lab/calibration.py").exists() else []
    if not p.exists():
        rec("C2", "§7.2 GTJA191 校准子集", "**未落地**", f"文件不存在: {p}")
        return
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        rec("C2", "§7.2 GTJA191 校准子集", "文件损坏", str(exc)[:120])
        return
    n = len(d) if isinstance(d, (list, dict)) else 0
    # 全仓引用扫描
    hits = []
    for f in ROOT.glob("backend/**/*.py"):
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "gtja191_subset" in t:
            hits.append(f.relative_to(ROOT).as_posix())
    rec("C2", "§7.2 GTJA191 校准子集", "已落地" if hits else "**落地但无引用**",
        f"文件存在，条目={n}\n引用点: {hits or '（全仓 0 处引用）'}")


# ── C3：c1/c2 对齐打分 ──
def c3() -> None:
    envk = _grep("backend/config/env_registry.py", r"FACTORS_LAB_ALIGN_MIN")
    code = []
    for f in ROOT.glob("backend/services/factors_lab/*.py"):
        t = f.read_text(encoding="utf-8", errors="replace")
        if "FACTORS_LAB_ALIGN_MIN" in t or "align_fail" in t:
            for i, ln in enumerate(t.splitlines(), 1):
                if "FACTORS_LAB_ALIGN_MIN" in ln or "align_fail" in ln:
                    code.append(f"{f.name}:{i} {ln.strip()[:88]}")
    rec("C3", "§7.3 c1/c2 语义对齐打分", "已落地" if code else "**未找到**",
        f"env_registry 声明: {[h[1][:70] for h in envk]}\n实现点:\n  " + "\n  ".join(code[:8]))


# ── C4：4 张战略表 ──
def c4() -> None:
    try:
        from sqlalchemy import text
        from backend.database.analytics_connection import AnalyticsSessionLocal  # type: ignore
        Sess = AnalyticsSessionLocal
    except Exception:
        try:
            from sqlalchemy import text
            from backend.database.connection import AnalyticsSessionLocal as Sess  # type: ignore
        except Exception as exc:
            rec("C4", "§8 B2 战略表", "无法判定", f"拿不到 analytics 会话: {str(exc)[:120]}")
            return
    try:
        db = Sess()
    except Exception as exc:
        rec("C4", "§8 B2 战略表", "无法判定", f"会话失败: {str(exc)[:120]}")
        return
    try:
        rows = db.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
            "AND table_name IN ('strategic_reports','macro_snapshots','memories','new_coin_opportunities')")
        ).fetchall()
        found = sorted(r[0] for r in rows)
        counts = {}
        for t in found:
            try:
                counts[t] = db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            except Exception as exc:
                counts[t] = f"查询失败 {str(exc)[:40]}"
        missing = sorted({"strategic_reports", "macro_snapshots", "memories",
                          "new_coin_opportunities"} - set(found))
        rec("C4", "§8 B2 战略表", "已建" if not missing else "**缺表**",
            f"存在: {found}\n行数: {counts}\n缺失: {missing or '无'}")
    finally:
        db.close()


# ── C5：.env K线分析师配额 ──
def c5() -> None:
    txt = _read(".env")
    m = re.search(r"^\s*KLINE_LLM_MAX_PER_CYCLE\s*=\s*(\S+)", txt, re.M)
    val = m.group(1) if m else None
    default = _grep("backend/config/settings.py", r"KLINE_LLM_MAX_PER_CYCLE")
    rec("C5", "§8 B3 K线分析师配额", "符合声明" if val == "16" else "与声明不符",
        f".env 实测 = {val}（声明 4→16）\n代码默认: {[h[1][:70] for h in default]}")


# ── C6：同因拦截冷却 ──
def c6() -> None:
    p = ROOT / "data/proposal_block_streaks.json"
    code = []
    for f in (ROOT / "backend/services/full_auto").glob("*.py"):
        t = f.read_text(encoding="utf-8", errors="replace")
        for i, ln in enumerate(t.splitlines(), 1):
            if re.search(r"proposal_block_streaks|block_streak|BlockCooldown|同因|冷却", ln):
                code.append(f"{f.name}:{i} {ln.strip()[:92]}")
    exists = p.exists()
    content = p.read_text(encoding="utf-8")[:200] if exists else ""
    rec("C6", "§10.1 同因拦截冷却", "已落地" if code else "**未找到**",
        f"streak 文件存在={exists}  内容={content!r}\n实现点（前 8）:\n  " + "\n  ".join(code[:8]))


# ── C7：strategy_detached 根治 ──
def c7() -> None:
    code = []
    for f in (ROOT / "backend/services/full_auto").glob("*.py"):
        t = f.read_text(encoding="utf-8", errors="replace")
        for i, ln in enumerate(t.splitlines(), 1):
            if re.search(r"auto_create_strategy|ensure_bound_strategy|strategy_detached", ln):
                code.append(f"{f.name}:{i} {ln.strip()[:94]}")
    # 运行时审计文件（有则说明该现象被观测到）
    audit = ROOT / "data/midlong_direction_audit.jsonl"
    n_audit = 0
    if audit.exists():
        t = audit.read_text(encoding="utf-8", errors="replace")
        n_audit = t.count("strategy_detached")
    rec("C7", "§11 strategy_detached 根治", "已落地" if code else "**未找到**",
        f"实现点（前 10）:\n  " + "\n  ".join(code[:10]) +
        f"\n审计文件 midlong_direction_audit.jsonl 中 strategy_detached 出现 {n_audit} 次"
        f"（文件存在={audit.exists()}）")


def main() -> int:
    print("=" * 84)
    print("六批实施记录 —— 未核验声明的逐条查证（只读）")
    print("=" * 84)
    for fn in (c1, c2, c3, c4, c5, c6, c7):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            rec(fn.__name__.upper(), "检查抛异常", "无法判定",
                f"{type(exc).__name__}: {str(exc)[:160]}")
    print("\n" + "=" * 84)
    for cid, title, verdict, _ in RESULTS:
        print(f"  {cid:<4} {verdict:<10} {title}")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
