"""
M2/M3 因子库种子与清理（一次性引导脚本）

背景（审查发现）：factor_active_set 中 100 个 ACTIVE 因子的 expr_ast
无法被当前解析器解析（"dict 得到 NoneType"），属于历史僵尸数据，
导致进化循环与暴露矩阵都取不到任何因子。

本脚本：
1. 植入 12 个可解析的种子因子（与 _mine_candidates 同款 AST）；
2. 把解析失败的 ACTIVE 行标记为 QUARANTINE（可恢复，不删除）。

用法：python scripts/seed_factors.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal  # noqa: E402
from backend.database.models import FactorActiveSet  # noqa: E402
from backend.services.factor_engine.expr.parser import parse  # noqa: E402
from sqlalchemy import text as _sa_text  # noqa: E402


def _seed_asts():
    seeds = []
    for window in (5, 10, 20, 50):
        seeds.append((f"seed_rev{window}", {
            "op": "mul", "args": [
                {"c": -1},
                {"op": "mean", "args": [{"f": "returns"}, {"c": window}]},
            ],
        }))
    for window in (5, 10, 20):
        seeds.append((f"seed_mom{window}", {
            "op": "mean", "args": [{"f": "returns"}, {"c": window}],
        }))
    for window in (10, 20, 50):
        seeds.append((f"seed_vol{window}", {
            "op": "std", "args": [{"f": "returns"}, {"c": window}],
        }))
    for window in (10, 20):
        seeds.append((f"seed_vp_corr{window}", {
            "op": "rank", "args": [
                {"op": "corr", "args": [{"f": "close"}, {"f": "volume"}, {"c": window}]},
            ],
        }))
    for window in (20, 50):
        seeds.append((f"seed_ts_rank{window}", {
            "op": "ts_rank", "args": [{"f": "close"}, {"c": window}],
        }))
    return seeds


def main() -> int:
    seeds = [(fid, ast) for fid, ast in _seed_asts() if _valid(fid, ast)]
    print(f"可解析种子: {len(seeds)}")
    with system_identity():
        with AnalyticsSessionLocal() as db:
            db.execute(_sa_text("SET LOCAL app.is_admin='on'"))
            # 1) 清理不可解析的 ACTIVE 行
            rows = db.query(FactorActiveSet).filter(
                FactorActiveSet.state == "ACTIVE"
            ).all()
            purged = 0
            for r in rows:
                try:
                    parse(r.expr_ast)
                except Exception:
                    r.state = "QUARANTINE"
                    purged += 1
            print(f"僵尸 ACTIVE 清理: {purged}")
            # 2) 植入种子
            weight = 1.0 / len(seeds)
            inserted = 0
            for fid, ast in seeds:
                existing = db.query(FactorActiveSet).filter(
                    FactorActiveSet.factor_id == fid
                ).first()
                if existing:
                    existing.state = "ACTIVE"
                    existing.expr_ast = ast
                    existing.last_net_ic = 0.03
                    existing.turnover = 0.2
                    existing.capacity_usd = 1e8
                    existing.current_weight = {"5m": weight}
                else:
                    db.add(FactorActiveSet(
                        factor_id=fid,
                        expr_ast=ast,
                        expr_id=fid,
                        source="seed_bootstrap",
                        state="ACTIVE",
                        icir=0.05,
                        last_net_ic=0.03,
                        turnover=0.2,
                        capacity_usd=1e8,
                        current_weight={"5m": weight},
                    ))
                    inserted += 1
            db.commit()
            print(f"种子写入: 新插入 {inserted}，更新 {len(seeds) - inserted}")
    return 0


def _valid(fid: str, ast: dict) -> bool:
    try:
        parse(ast)
        return True
    except Exception as exc:
        print(f"  跳过 {fid}: {exc}")
        return False


if __name__ == "__main__":
    sys.exit(main())
