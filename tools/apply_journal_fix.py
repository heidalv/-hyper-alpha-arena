#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性补丁: journal 数据源换 trade_facts + 保存幂等(2026-08-25)。"""
import shutil

PATH = "backend/services/ai_trade_journal_service.py"
BAK = PATH + ".bak_20260825"
shutil.copy(PATH, BAK)
print("backup ->", BAK)

src = open(PATH, encoding="utf-8").read()

# 编辑1: _collect_trades 换数据源
old1 = '''    def _collect_trades(self, db: Session, date_str: str, period: str) -> List[Dict]:
        """收集指定日期的交易记录"""
        try:
            from backend.database.models import PaperOrder
            rows = (
                db.query(PaperOrder)
                .filter(PaperOrder.status == "filled")
                .order_by(PaperOrder.created_at.desc())
                .limit(200)
                .all()
            )
            trades = []
            for r in rows:
                created = str(r.created_at) if r.created_at else ""
                if date_str in created:
                    trades.append({
                        "symbol": r.symbol,
                        "side": r.side,
                        "quantity": r.quantity,
                        "price": r.filled_price or r.price,
                        "pnl": getattr(r, "pnl", 0) or 0,
                        "strategy_id": getattr(r, "strategy_id", ""),
                        "created_at": created,
                    })
            return trades
        except Exception as e:
            logger.debug(f"[TradeJournal] 收集交易记录异常: {e}")
            return []'''
new1 = '''    def _collect_trades(self, db: Session, date_str: str, period: str) -> List[Dict]:
        """收集指定日期的交易记录,数据源 trade_facts(paper_orders 已被清空)。"""
        try:
            from sqlalchemy import text as _sa_text
            rows = db.execute(_sa_text(
                "SELECT ts, symbol, tier, side, entry_price, exit_price, fees, pnl, "
                "outcome, close_reason, position_id "
                "FROM trade_facts WHERE ts::date = :d ORDER BY ts"
            ), {"d": date_str}).fetchall()
            trades = []
            for r in rows:
                trades.append({
                    "symbol": r.symbol,
                    "side": r.side,
                    "tier": r.tier,
                    "quantity": 0,
                    "price": r.exit_price,
                    "pnl": float(r.pnl or 0),
                    "strategy_id": "",
                    "close_reason": r.close_reason or "",
                    "created_at": str(r.ts),
                })
            return trades
        except Exception as e:
            logger.debug(f"[TradeJournal] 收集交易记录异常: {e}")
            return []'''
assert src.count(old1) == 1, f"edit1 count={src.count(old1)}"
src = src.replace(old1, new1)

# 编辑2: _collect_trades_range 整体替换
old2 = '''    def _collect_trades_range(self, db: Session, start: str, end: str) -> List[Dict]:'''
assert src.count(old2) == 1, f"edit2 anchor count={src.count(old2)}"
i0 = src.index(old2)
i1 = src.index("\n    def ", i0 + len(old2))
body_new = '''    def _collect_trades_range(self, db: Session, start: str, end: str) -> List[Dict]:
        """收集日期范围内的交易记录,数据源 trade_facts。"""
        try:
            from sqlalchemy import text as _sa_text
            rows = db.execute(_sa_text(
                "SELECT ts, symbol, tier, side, entry_price, exit_price, fees, pnl, "
                "outcome, close_reason, position_id "
                "FROM trade_facts WHERE ts >= :s AND ts < :e ORDER BY ts"
            ), {"s": start, "e": end}).fetchall()
            return [
                {
                    "symbol": r.symbol, "side": r.side, "tier": r.tier,
                    "quantity": 0, "price": r.exit_price,
                    "pnl": float(r.pnl or 0), "strategy_id": "",
                    "close_reason": r.close_reason or "", "created_at": str(r.ts),
                }
                for r in rows
            ]
        except Exception as e:
            logger.debug(f"[TradeJournal] 收集范围交易异常: {e}")
            return []'''
src = src[:i0] + body_new + src[i1:]

# 编辑3: _save_journal 幂等 upsert
old3 = '''    def _save_journal(self, db: Session, result: Dict):
        from backend.database.models import TradeJournal
        journal = TradeJournal('''
new3 = '''    def _save_journal(self, db: Session, result: Dict):
        from backend.database.models import TradeJournal
        _pt = result.get("period_type", "daily")
        _pd = result.get("period_date", "")
        db.query(TradeJournal).filter(
            TradeJournal.period_type == _pt,
            TradeJournal.period_date == _pd,
        ).delete(synchronize_session=False)
        journal = TradeJournal('''
assert src.count(old3) == 1, f"edit3 count={src.count(old3)}"
src = src.replace(old3, new3)

# 编辑4: 插入行字段用局部变量
old4 = '''        journal = TradeJournal(
            period_type=result.get("period_type", "daily"),
            period_date=result.get("period_date", ""),'''
new4 = '''        journal = TradeJournal(
            period_type=_pt,
            period_date=_pd,'''
assert src.count(old4) == 1, f"edit4 count={src.count(old4)}"
src = src.replace(old4, new4)

open(PATH, "w", encoding="utf-8").write(src)
print("patched OK")
