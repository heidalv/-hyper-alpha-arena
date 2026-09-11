# -*- coding: utf-8 -*-
"""v3 F2e：模拟账户权益重置（保留全部历史）+ 会话风控基线归零 + 止血配置落库。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe -m backend.scripts.v3_paper_reset --account 14 --equity 5000 --session 10
    加 --dry-run 只打印不落库。

做了什么
--------
1. 快照：重置前先落两份边际账本快照（30 天 / 90 天）到 edge_ledger_snapshots，并把
   paper_balances 行 + 未平仓位 + 会话风控字段 dump 到 backend/data/paper_reset_snapshots/。
   历史订单/持仓/trade_facts **一律不删**（边际账本、学习层都还要用）。
2. 软重置：initial_balance=目标权益，last_reset_at=now，然后按引擎 _recalc_balance 重算
   （v3 起 recalc 只汇总 last_reset_at 之后的订单/资金费，重置才真正生效）。
   未平仓位保留：保证金照常占用，后续平仓盈亏计入新曲线。
3. 会话基线：peak_balance=重置后权益、current_drawdown=max_drawdown=0、
   total_pnl/total_trades/winning_trades=0（session_stats 只统计 opened_at ≥ last_reset_at 的仓）。
4. 止血配置：会话 auto_coin_mid_enabled=false（mid/long 只留固定主流币；auto-coin 只喂影子短线）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)


def _dump_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, required=True, help="paper 账户 id（paper_balances.account_id）")
    ap.add_argument("--equity", type=float, default=5000.0, help="重置后的初始权益")
    ap.add_argument("--session", type=int, default=None, help="full_auto_sessions.id（可选）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sqlalchemy import text
    from backend.database.connection import SessionLocal
    from backend.database.models import PaperBalance, FullAutoSession, PaperPosition
    from backend.services.ledger.edge_ledger import compute_edge_ledger, persist_snapshot
    from backend.services.paper_trading_engine import paper_engine

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = _ROOT / "backend" / "data" / "paper_reset_snapshots"

    with SessionLocal() as db:
        bal = db.query(PaperBalance).filter(PaperBalance.account_id == args.account).first()
        if not bal:
            print(f"[reset] paper 账户 {args.account} 不存在")
            return 2
        before = {
            "account_id": bal.account_id, "initial_balance": bal.initial_balance,
            "total_equity": bal.total_equity, "available_balance": bal.available_balance,
            "frozen_margin": bal.frozen_margin, "realized_pnl": bal.realized_pnl,
            "total_fee_paid": bal.total_fee_paid, "last_reset_at": bal.last_reset_at,
        }
        open_pos = db.query(PaperPosition).filter(
            PaperPosition.account_id == args.account, PaperPosition.status == "open"
        ).all()
        open_dump = [
            {"id": p.id, "symbol": p.symbol, "side": p.side, "size": p.size, "entry": p.entry_price,
             "margin": p.margin, "lev": p.leverage, "tier": p.timeframe_tier, "nature": p.trade_nature,
             "upnl": p.unrealized_pnl, "opened_at": p.opened_at}
            for p in open_pos
        ]
        sess = db.query(FullAutoSession).filter(FullAutoSession.id == args.session).first() if args.session else None
        sess_dump = None
        if sess:
            sess_dump = {
                "id": sess.id, "status": sess.status, "pause_reason": sess.pause_reason,
                "peak_balance": sess.peak_balance, "current_drawdown": sess.current_drawdown,
                "max_drawdown": sess.max_drawdown, "total_pnl": sess.total_pnl,
                "total_trades": sess.total_trades, "winning_trades": sess.winning_trades,
                "auto_coin_enabled": sess.auto_coin_enabled,
                "auto_coin_mid_enabled": getattr(sess, "auto_coin_mid_enabled", None),
                "max_total_drawdown_pct": sess.max_total_drawdown_pct,
                "daily_loss_limit_pct": sess.daily_loss_limit_pct,
            }

        print("[reset] 重置前:", json.dumps(before, ensure_ascii=False, default=str))
        print(f"[reset] 未平仓位 {len(open_dump)} 笔（保留）")
        if sess_dump:
            print("[reset] 会话:", json.dumps(sess_dump, ensure_ascii=False, default=str))

        # 1) 快照（不受 dry-run 影响：只读+落快照表，无副作用于交易）
        snaps = {}
        for d in (30, 90):
            try:
                snap = compute_edge_ledger(db, args.account, d)
                sid = None if args.dry_run else persist_snapshot(db, snap)
                snaps[str(d)] = {"snapshot_id": sid, "total": snap.get("total"), "by_tier": snap.get("by_tier")}
                print(f"[reset] 边际账本 {d}d: id={sid} total={snap.get('total')}")
            except Exception as exc:
                print(f"[reset] 边际账本 {d}d 计算失败: {exc}")
        _dump_json(out_dir / f"acct{args.account}_{ts}.json", {
            "before": before, "open_positions": open_dump, "session": sess_dump, "edge_ledger": snaps,
            "target_equity": args.equity, "dry_run": args.dry_run, "ts": ts,
        })
        print(f"[reset] 快照已写 {out_dir / f'acct{args.account}_{ts}.json'}")

        if args.dry_run:
            print("[reset] dry-run，未落库")
            return 0

        # 2) 软重置
        now_utc = datetime.now(timezone.utc)
        bal.initial_balance = float(args.equity)
        bal.last_reset_at = now_utc
        db.flush()
        paper_engine._recalc_balance(db, bal)
        db.commit()
        db.refresh(bal)
        after = paper_engine._balance_to_dict(bal)
        print("[reset] 重置后:", json.dumps(after, ensure_ascii=False, default=str))

        # 3) 会话基线 + 4) 止血配置
        if sess:
            sess.peak_balance = round(float(bal.total_equity or args.equity), 4)
            sess.current_drawdown = 0.0
            sess.max_drawdown = 0.0
            sess.total_pnl = 0.0
            sess.total_trades = 0
            sess.winning_trades = 0
            try:
                sess.auto_coin_mid_enabled = False
            except Exception:
                pass
            db.commit()
            print(f"[reset] 会话 {sess.id} 基线已归零：peak={sess.peak_balance} dd=0 auto_coin_mid_enabled=False")
            # 兜底：ORM 无该列时直接 SQL
            try:
                db.execute(text("UPDATE full_auto_sessions SET auto_coin_mid_enabled = FALSE WHERE id = :i"), {"i": sess.id})
                db.commit()
            except Exception as exc:
                print(f"[reset] auto_coin_mid_enabled SQL 兜底失败: {exc}")
                db.rollback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
