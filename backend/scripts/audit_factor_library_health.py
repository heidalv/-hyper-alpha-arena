# -*- coding: utf-8 -*-
"""因子库健康审计（中长线）：漏斗存活率 / 种子同质性 / 评估口径。

2026-09-09 根因调查的固化脚本。回答三个问题：
  1. 挖掘管道到底在哪一步把候选杀光？（漏斗存活率）
  2. 种子池是不是同质的？（探索空间覆盖）
  3. 晋升门用的口径与"费后净收益"是否一致？（口径有效性）

用法：
    .venv\\Scripts\\python.exe backend/scripts/audit_factor_library_health.py
输出：控制台表格 + `data/factor_library_health.json`
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ANALYTICS_URL = os.getenv("ANALYTICS_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
OUT = ROOT / "data" / "factor_library_health.json"


def main() -> int:
    eng = create_engine(ANALYTICS_URL)
    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "alpha_analytics: factor_active_set / factor_evolution_log / factor_performance_logs",
    }
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))

        # ── 1) 存活集 ──
        rows = [dict(r._mapping) for r in c.execute(text("""
            select factor_id, source, state, icir, last_net_ic, turnover, period,
                   activated_at, deactivated_at, evaluated_cycles, expr_ast
            from factor_active_set order by state, source
        """)).fetchall()]
        states = Counter(r["state"] for r in rows)
        report["active_set"] = {
            "total": len(rows),
            "by_state": dict(states),
            "rows": [
                {k: (str(v) if k in ("activated_at", "deactivated_at") else v)
                 for k, v in r.items()} for r in rows
            ],
        }
        print("=== 存活集 ===")
        print(f"  总数 {len(rows)}  状态 {dict(states)}")
        for r in rows:
            _seed = str(r["source"] or "").startswith("seed_bootstrap")
            print(f"   {r['state']:<11} {str(r['source']):<22} icir={r['icir']} net_ic={r['last_net_ic']}"
                  f"{'  ← seed_bootstrap 占位' if _seed else ''}")

        # 同质性：表达式族
        fam = Counter()
        for r in rows:
            src = str(r["source"] or "")
            if src.startswith(("rev", "seed_rev")):
                fam["time_series_reversal"] += 1
            elif src.startswith(("mom", "seed_mom")):
                fam["time_series_momentum"] += 1
            elif src.startswith(("vol", "seed_vol")):
                fam["volatility"] += 1
            elif src.startswith(("ts_rank", "seed_ts_rank")):
                fam["time_series_rank"] += 1
            elif src.startswith(("vp_corr", "seed_vp_corr")):
                fam["volume_price_corr"] += 1
            elif src.startswith("mcts"):
                fam["mcts_mined"] += 1
            else:
                fam[f"other:{src[:16]}"] += 1
        report["families"] = dict(fam)
        print(f"\n=== 因子族分布 ===\n  {dict(fam)}")

        # ── 1b) 表达式空间覆盖度（2026-09-09 新增）──
        # 根因：18 个种子的 expr_ast 只用到 3 个基础字段 + 6 个算子，
        # MCTS 从这个池子出发只能在同一区域变异 → 不可能挖出增量 alpha。
        def _walk(node, fields, ops):
            if isinstance(node, dict):
                if "f" in node:
                    fields.add(str(node["f"]))
                if "op" in node:
                    ops.add(str(node["op"]))
                for v in node.values():
                    _walk(v, fields, ops)
            elif isinstance(node, (list, tuple)):
                for v in node:
                    _walk(v, fields, ops)

        fields, ops = set(), set()
        n_with_ast = 0
        for r in rows:
            ast = r.get("expr_ast")
            if isinstance(ast, str):
                try:
                    ast = json.loads(ast)
                except Exception:
                    ast = None
            if not ast:
                continue
            n_with_ast += 1
            _walk(ast, fields, ops)
        report["expr_space"] = {
            "n_with_ast": n_with_ast,
            "distinct_fields": sorted(fields),
            "distinct_ops": sorted(ops),
        }
        print(f"\n=== 表达式空间覆盖（{n_with_ast} 个带 AST 的因子）===")
        print(f"  基础字段 {len(fields)}: {sorted(fields)}")
        print(f"  算子     {len(ops)}: {sorted(ops)}")

        # ── 2) 漏斗 ──
        funnel = [dict(r._mapping) for r in c.execute(text("""
            select phase, action, count(*) n, count(distinct factor_id) f
            from factor_evolution_log group by 1,2 order by n desc
        """)).fetchall()]
        report["funnel"] = [{k: (str(v) if k in ("mn", "mx") else v) for k, v in r.items()} for r in funnel]
        print("\n=== 漏斗（全部历史）===")
        for r in funnel[:16]:
            print(f"   {str(r['phase']):<14} {str(r['action']):<24} n={r['n']:<6} distinct_factor={r['f']}")

        # 近 30 天 reject 原因
        cnt = Counter()
        for (reason,) in c.execute(text("""
            select reason from factor_evolution_log
            where action like '%reject%' and created_at > now() - interval '30 days' limit 20000
        """)):
            s = str(reason or "")
            key = s.split(":")[1] if ":" in s else s[:40]
            cnt[key] += 1
        report["reject_reasons_30d"] = dict(cnt.most_common(20))
        print("\n=== 近 30 天 reject 原因（前缀）===")
        for k, v in cnt.most_common(12):
            print(f"   {k}: {v}")

        # ── 3) IC 口径 ──
        ic = [dict(r._mapping) for r in c.execute(text("""
            select factor_category, timeframe, count(*) n,
                   round(avg(ic_value)::numeric, 4) avg_ic,
                   round(avg(abs(ic_value))::numeric, 4) avg_abs_ic
            from factor_performance_logs
            where ic_value is not null and recorded_at > now() - interval '30 days'
            group by 1,2 order by n desc
        """)).fetchall()]
        report["ic_by_category_tf_30d"] = [{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in r.items()} for r in ic]
        print("\n=== IC 口径（近 30 天）===")
        for r in ic:
            print(f"   {r['factor_category']:<14} {str(r['timeframe']):<5} n={r['n']:<8} avg_ic={r['avg_ic']} |avg|={r['avg_abs_ic']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n已写入 {OUT}")

    # 结论提示（ASCII，避免 GBK 控制台报错）
    act = states.get("ACTIVE", 0)
    if act < 5:
        print(f"[WARN] ACTIVE 因子仅 {act} 个 -> 单一因子失效即全车道无弹药")
    if len(fam) <= 3:
        print(f"[WARN] 因子族仅 {len(fam)} 类 -> 探索空间同质，MCTS 只能在同一区域打转")
    # 种子池覆盖度：字段/算子维度太低即说明探索空间被人为收窄
    try:
        from backend.services.factor_engine.expr.ops import ALLOWED_FIELDS, OP_REGISTRY
        _used_f = len(report.get("expr_space", {}).get("distinct_fields") or [])
        _used_o = len(report.get("expr_space", {}).get("distinct_ops") or [])
        print(f"[INFO] 表达式语言可用 {len(ALLOWED_FIELDS)} 字段 / {len(OP_REGISTRY)} 算子；"
              f"存活因子实际只用到 {_used_f} 字段 / {_used_o} 算子")
        if _used_f <= 4:
            print("[WARN] 存活因子只覆盖 <=4 个字段 -> 种子池人为收窄，建议按 §种子池扩充 加入 "
                  "vwap/amount/turnover/funding/oi/basis/upper_wick 等家族")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
