# -*- coding: utf-8 -*-
"""[2026-09-11] 清理 e2e 学习链测试写入生产库的污染行（默认干跑，--apply 落库）。

背景：backend/tests/test_e2e_learning_chain.py 直跑时（python -m）把
e2e_* 测试策略/交易写进生产 Postgres；9/11 06:21/06:50 两批共 16+ 行
strategy_trades（合成阶梯亏损 pnl=-20..-27），使 7 天 PnL 统计被污染
（-2634.7 中 -2417 是测试假数据）。

清理对象（按 strategy_id 前缀 e2e_ / test_e2e_）：
  - strategy_trades（合成交易行）
  - paper_orders / paper_positions（若存在，e2e 会话的纸面订单/持仓）
  - ai_strategies（测试策略行）
删除前全部备份到 data/e2e_pollution_backup_<ts>.jsonl。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv(".env", override=True)

from sqlalchemy import text
from backend.database.connection import SessionLocal


def _row_to_dict(row):
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正删除；缺省只干跑并打印")
    args = ap.parse_args()

    db = SessionLocal()
    tables = ["strategy_trades", "paper_orders", "paper_positions", "ai_strategies"]
    backup_rows: list[dict] = []
    counts: dict[str, int] = {}
    try:
        db.execute(text("SET app.is_admin='on'"))
        for tbl in tables:
            exists = db.execute(text(
                "SELECT 1 FROM information_schema.tables WHERE table_name=:t"
            ), {"t": tbl}).scalar()
            if not exists:
                print(f"{tbl}: 表不存在，跳过")
                continue
            # 前缀匹配列：strategy_trades/paper_* 用 strategy_id；ai_strategies 用 strategy_id
            cols = [c[0] for c in db.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name=:t"
            ), {"t": tbl}).fetchall()]
            if "strategy_id" not in cols:
                print(f"{tbl}: 无 strategy_id 列，跳过")
                continue
            rows = db.execute(text(
                f"SELECT * FROM {tbl} WHERE strategy_id LIKE 'e2e_%' OR strategy_id LIKE 'test_e2e_%'"
            )).mappings().all()
            if not rows:
                print(f"{tbl}: 0 行")
                continue
            counts[tbl] = len(rows)
            for r in rows:
                backup_rows.append({"table": tbl, "row": _row_to_dict(r)})
            print(f"{tbl}: {len(rows)} 行{' [干跑，未删除]' if not args.apply else ''}")
            if args.apply:
                # ai_strategies 有 FK 依赖（prompt_training_records / signal_performance_history /
                # strategy_memories / strategy_trades），先清依赖行再删策略行。
                if tbl == "ai_strategies":
                    deps = db.execute(text("""
                        SELECT cl.relname AS src
                        FROM pg_constraint c
                        JOIN pg_class cl ON cl.oid=c.conrelid
                        JOIN pg_class t ON t.oid=c.confrelid
                        WHERE c.contype='f' AND t.relname='ai_strategies'
                    """)).mappings().all()
                    for d in deps:
                        dep = d["src"]
                        if dep == "strategy_trades":
                            continue  # 上面循环已删
                        dep_n = db.execute(text(
                            f"DELETE FROM {dep} WHERE strategy_id LIKE 'e2e_%' OR strategy_id LIKE 'test_e2e_%'"
                        )).rowcount
                        if dep_n:
                            print(f"  FK 依赖 {dep}: 已删除 {dep_n} 行")
                        db.commit()
                db.execute(text(
                    f"DELETE FROM {tbl} WHERE strategy_id LIKE 'e2e_%' OR strategy_id LIKE 'test_e2e_%'"
                ))
                db.commit()
                print(f"{tbl}: 已删除 {len(rows)} 行")
    finally:
        db.close()

    if backup_rows:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path("data") / f"e2e_pollution_backup_{ts}.jsonl"
        out.parent.mkdir(exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            for r in backup_rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        print(f"备份已写入 {out}（{len(backup_rows)} 行）")
    print("done. counts=", counts)


if __name__ == "__main__":
    main()
