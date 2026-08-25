#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性补丁: trade_facts 落库 factor_exposures + strategy_id(2026-08-25)。"""
import shutil

PATH = "backend/services/paper_trading_engine.py"
BAK = PATH + ".bak_20260825_factexpo"
shutil.copy(PATH, BAK)
print("backup ->", BAK)

src = open(PATH, encoding="utf-8").read()

# 编辑1: 调用点增加 factor_exposures 查询 + strategy_id 传入
old1 = '''        # ── 飞书通知：平仓事件 ──
        # M10 样本仓库：统一交易事实表
        try:
            self._write_trade_fact(
                account_id=account_id,
                position_id=str(getattr(pos, "id", "") or ""),
                symbol=symbol,
                tier=_close_tier,
                side=side,
                entry_price=float(getattr(pos, "entry_price", 0) or 0),
                exit_price=float(fill_price or 0),
                fees=float(total_fee or 0),
                pnl=float(total_pnl or 0),
                outcome=("win" if (total_pnl or 0) > 0
                         else ("loss" if (total_pnl or 0) < 0 else "scratch")),
                close_reason=actual_reason,
            )
        except Exception as _tf_err:
            logger.debug(f"[Paper] trade_fact 写入失败: {_tf_err}")'''
new1 = '''        # ── 飞书通知：平仓事件 ──
        # M10 样本仓库：统一交易事实表
        # [2026-08-25] 补落 factor_exposures(开仓时点最近同向信号的因子快照)
        # 与 strategy_id,使复盘/学习层恢复因子归因与策略归因。
        _fx = None
        try:
            from sqlalchemy import text as _sa_text
            from backend.database.connection import SessionLocal as _ArenaLocal2
            _opened = getattr(pos, "opened_at", None)
            if _opened is not None:
                try:
                    _opened_naive = _opened.replace(tzinfo=None) if _opened.tzinfo is not None else _opened
                except Exception:
                    _opened_naive = _opened
                with _ArenaLocal2() as _db2:
                    _fx_row = _db2.execute(_sa_text(
                        "SELECT features_json FROM scalp_signal_log "
                        "WHERE symbol=:s AND direction=:d AND created_at <= :t "
                        "ORDER BY created_at DESC LIMIT 1"
                    ), {"s": str(symbol).upper(), "d": str(side), "t": _opened_naive}).first()
                    if _fx_row and _fx_row[0]:
                        import json as _json
                        _fx = _json.loads(_fx_row[0]) if isinstance(_fx_row[0], str) else _fx_row[0]
        except Exception as _fx_err:
            logger.debug(f"[Paper] factor_exposures 查询跳过: {_fx_err}")

        try:
            self._write_trade_fact(
                account_id=account_id,
                position_id=str(getattr(pos, "id", "") or ""),
                symbol=symbol,
                tier=_close_tier,
                side=side,
                entry_price=float(getattr(pos, "entry_price", 0) or 0),
                exit_price=float(fill_price or 0),
                fees=float(total_fee or 0),
                pnl=float(total_pnl or 0),
                outcome=("win" if (total_pnl or 0) > 0
                         else ("loss" if (total_pnl or 0) < 0 else "scratch")),
                close_reason=actual_reason,
                factor_exposures=_fx,
                strategy_id=str(getattr(pos, "strategy_id", "") or ""),
            )
        except Exception as _tf_err:
            logger.debug(f"[Paper] trade_fact 写入失败: {_tf_err}")'''
assert src.count(old1) == 1, f"edit1 count={src.count(old1)}"
src = src.replace(old1, new1)

# 编辑2: _write_trade_fact 签名与 INSERT
old2 = '''    @staticmethod
    def _write_trade_fact(
        *,
        account_id: int,
        position_id: str,
        symbol: str,
        tier: str,
        side: str,
        entry_price: float,
        exit_price: float,
        fees: float,
        pnl: float,
        outcome: str,
        close_reason: str,
    ) -> None:'''
new2 = '''    @staticmethod
    def _write_trade_fact(
        *,
        account_id: int,
        position_id: str,
        symbol: str,
        tier: str,
        side: str,
        entry_price: float,
        exit_price: float,
        fees: float,
        pnl: float,
        outcome: str,
        close_reason: str,
        factor_exposures: Any = None,
        strategy_id: str = "",
    ) -> None:'''
assert src.count(old2) == 1, f"edit2 count={src.count(old2)}"
src = src.replace(old2, new2)

old3 = '''                _db.execute(_sa_text(
                    "INSERT INTO trade_facts "
                    "(source, account_id, position_id, symbol, tier, side, entry_price, "
                    " exit_price, fees, pnl, outcome, close_reason) "
                    "VALUES ('paper', :a, :p, :s, :t, :d, :e, :x, :f, :pnl, :o, :r)"
                ), {
                    "a": int(account_id), "p": position_id, "s": str(symbol).upper(),
                    "t": str(tier or "short"), "d": str(side or ""),
                    "e": float(entry_price or 0), "x": float(exit_price or 0),
                    "f": float(fees or 0), "pnl": float(pnl or 0),
                    "o": str(outcome or ""), "r": str(close_reason or ""),
                })'''
new3 = '''                # [2026-08-25] 补 strategy_id 列(旧表 ALTER 一次;JSONB 已建)
                try:
                    _db.execute(_sa_text(
                        "ALTER TABLE trade_facts ADD COLUMN IF NOT EXISTS "
                        "strategy_id VARCHAR(64) NOT NULL DEFAULT ''"
                    ))
                except Exception:
                    pass
                import json as _json
                _fx_json = None
                if factor_exposures:
                    try:
                        _fx_json = _json.dumps(factor_exposures, ensure_ascii=False)
                    except Exception:
                        _fx_json = None
                _db.execute(_sa_text(
                    "INSERT INTO trade_facts "
                    "(source, account_id, position_id, symbol, tier, side, entry_price, "
                    " exit_price, fees, pnl, outcome, close_reason, factor_exposures, strategy_id) "
                    "VALUES ('paper', :a, :p, :s, :t, :d, :e, :x, :f, :pnl, :o, :r, "
                    " CAST(:fx AS JSONB), :sid)"
                ), {
                    "a": int(account_id), "p": position_id, "s": str(symbol).upper(),
                    "t": str(tier or "short"), "d": str(side or ""),
                    "e": float(entry_price or 0), "x": float(exit_price or 0),
                    "f": float(fees or 0), "pnl": float(pnl or 0),
                    "o": str(outcome or ""), "r": str(close_reason or ""),
                    "fx": _fx_json if _fx_json else "null",
                    "sid": str(strategy_id or ""),
                })'''
assert src.count(old3) == 1, f"edit3 count={src.count(old3)}"
src = src.replace(old3, new3)

open(PATH, "w", encoding="utf-8").write(src)
print("patched OK")
