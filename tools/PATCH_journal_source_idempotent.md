# P1 补丁: 交易复盘(journal)数据源与幂等修复
# 根因: _collect_trades 从 paper_orders 读(表已被清空 → 0 行/残缺),
#       _save_journal 每次 INSERT 新行(无唯一约束 → 同日 19 行漂移)。
# 修法: 数据源换 trade_facts(M10 持久事实表);保存改 upsert。

# ── 修改 1: ai_trade_journal_service.py _collect_trades 换数据源 ──
    def _collect_trades(self, db: Session, date_str: str, period: str) -> List[Dict]:
        """收集指定日期的交易记录 —— 数据源: trade_facts(paper_orders 已被清空)。"""
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
                    "symbol": r.symbol, "side": r.side, "tier": r.tier,
                    "quantity": 0, "price": r.exit_price,
                    "pnl": float(r.pnl or 0),
                    "strategy_id": "",  # trade_facts 无策略字段(P1-3 补列后再归因)
                    "close_reason": r.close_reason or "",
                    "created_at": str(r.ts),
                })
            return trades
        except Exception as e:
            logger.debug(f"[TradeJournal] 收集交易记录异常: {e}")
            return []

# ── 修改 2: _collect_trades_range 同理换 trade_facts ──
    def _collect_trades_range(self, db: Session, start: str, end: str) -> List[Dict]:
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
            return []

# ── 修改 3: _save_journal 改 upsert(同日同期只留一行)──
    def _save_journal(self, db: Session, result: Dict):
        from backend.database.models import TradeJournal
        _pt = result.get("period_type", "daily")
        _pd = result.get("period_date", "")
        # 幂等: 先删同日同期旧行,再插入(避免重启/手动触发产生漂移副本)
        db.query(TradeJournal).filter(
            TradeJournal.period_type == _pt,
            TradeJournal.period_date == _pd,
        ).delete(synchronize_session=False)
        journal = TradeJournal(
            period_type=_pt, period_date=_pd,
            total_trades=result.get("total_trades", 0),
            total_pnl=result.get("total_pnl", 0),
            win_rate=result.get("win_rate", 0),
            best_strategy=result.get("best_strategy", ""),
            worst_strategy=result.get("worst_strategy", ""),
            ai_analysis=result.get("ai_analysis", ""),
            improvement_actions=result.get("improvement_actions", []),
        )
        db.add(journal)
        try:
            db.commit()
        except Exception:
            db.rollback()

# ── 修改 4(可选,下一批): trade_facts 增列 strategy_id + 写入时落库,
#    让复盘恢复策略归因(paper_positions 已被清空,历史不可恢复)。
