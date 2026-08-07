"""
一次性历史数据清洗 — 交易对 symbol 去重归一化（幂等，可重复执行）

背景（2026-08-07 数据中心价格与交易对修复）：
- crypto_klines 曾并存标准格式（BTC）与历史残留格式（BTC-PERP / BTCUSDT 等），
  同一交易对多条目、多价格 → /overview/all 重复行、价格不一致的根因。
- symbol_catalog 存在中文脏数据（"龙虾"/"币安人生"/"我踏马来了"）。

处理步骤：
1. 执行前用 sqlite3 backup API 备份 alpha_market.db（一致性快照，带时间戳）。
2. crypto_klines：
   a. 非法 symbol（非 [A-Z0-9] 2-20 位，如中文残留）→ 删除
   b. 残留格式（normalize 后 != 原样，如 BTC-PERP → BTC）：
      - 与 base 行 (exchange, period, timestamp) 冲突 → 删除残留行（保留权威 base 行）
      - 无冲突 → UPDATE symbol = base（不丢数据）
3. symbol_catalog：同上（非法删除 + 残留合并），撞唯一约束时先删冲突行再更新。

用法：
  python scripts/cleanup_symbol_dedupe.py            # 执行（自动备份）
  python scripts/cleanup_symbol_dedupe.py --dry-run  # 只统计不执行

退出码：0=完成（或 dry-run）；1=异常。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.symbol_normalizer import is_valid_base_symbol, normalize_symbol  # noqa: E402

MARKET_DB = ROOT / "data" / "alpha_market.db"
BACKUP_DIR = ROOT / "data" / "backup"


def _now_tag() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")


def backup_db(dry_run: bool) -> Path | None:
    """sqlite3 backup API 一致性快照。"""
    if not MARKET_DB.exists():
        print(f"[backup] 跳过：{MARKET_DB} 不存在")
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f"alpha_market.db.cleanup-{_now_tag()}.bak"
    if dry_run:
        print(f"[backup] (dry-run) 将备份到 {dst}")
        return None
    src = sqlite3.connect(str(MARKET_DB))
    try:
        d = sqlite3.connect(str(dst))
        try:
            src.backup(d)
        finally:
            d.close()
    finally:
        src.close()
    size_mb = dst.stat().st_size / 1024 / 1024
    print(f"[backup] 已备份 -> {dst} ({size_mb:.1f} MB)")
    return dst


def cleanup_klines(db: sqlite3.Connection, dry_run: bool) -> dict:
    """crypto_klines 残留格式合并 + 非法行删除。返回统计。"""
    stats = {"dirty_sym": 0, "invalid_rows": 0, "conflict_del": 0, "merged_upd": 0, "processed": 0}
    cur = db.cursor()
    rows = cur.execute(
        "SELECT DISTINCT exchange, symbol FROM crypto_klines ORDER BY exchange, symbol"
    ).fetchall()
    targets: list[tuple[str, str, str]] = []  # (exchange, dirty_symbol, base)
    invalid: list[tuple[str, str]] = []
    for ex, sym in rows:
        if sym is None:
            continue
        s = str(sym).strip()
        if not s:
            continue
        if not is_valid_base_symbol(s):
            invalid.append((ex, s))
            continue
        base = normalize_symbol(s)
        if base and base != s:
            targets.append((ex, s, base))
    stats["dirty_sym"] = len(targets)
    if invalid:
        print(f"[crypto_klines] 非法 symbol {len(invalid)} 组: {invalid[:10]}{'...' if len(invalid) > 10 else ''}")
    for ex, s in invalid:
        stats["invalid_rows"] += cur.execute(
            "SELECT COUNT(*) FROM crypto_klines WHERE exchange=? AND symbol=?", (ex, s)
        ).fetchone()[0]
        if not dry_run:
            cur.execute("DELETE FROM crypto_klines WHERE exchange=? AND symbol=?", (ex, s))
    for ex, dirty, base in targets:
        if not dry_run:
            # 1) 冲突删除：与 base 行同 (period, timestamp) 的残留行 → 删
            cur.execute(
                """
                DELETE FROM crypto_klines
                WHERE exchange = :ex AND symbol = :dirty
                  AND (period, timestamp) IN (
                      SELECT period, timestamp FROM crypto_klines
                      WHERE exchange = :ex AND symbol = :base
                  )
                """,
                {"ex": ex, "dirty": dirty, "base": base},
            )
            stats["conflict_del"] += cur.rowcount
            # 2) 无冲突残留 → 更新为 base（不丢数据）
            cur.execute(
                "UPDATE crypto_klines SET symbol = :base WHERE exchange = :ex AND symbol = :dirty",
                {"ex": ex, "dirty": dirty, "base": base},
            )
            stats["merged_upd"] += cur.rowcount
        else:
            n_conflict = cur.execute(
                """
                SELECT COUNT(*) FROM crypto_klines
                WHERE exchange = :ex AND symbol = :dirty
                  AND (period, timestamp) IN (
                      SELECT period, timestamp FROM crypto_klines
                      WHERE exchange = :ex AND symbol = :base
                  )
                """,
                {"ex": ex, "dirty": dirty, "base": base},
            ).fetchone()[0]
            n_total = cur.execute(
                "SELECT COUNT(*) FROM crypto_klines WHERE exchange=? AND symbol=?",
                (ex, dirty),
            ).fetchone()[0]
            stats["conflict_del"] += n_conflict
            stats["merged_upd"] += n_total - n_conflict
    if not dry_run:
        db.commit()
    stats["processed"] = len(targets)
    return stats


def cleanup_catalog(db: sqlite3.Connection, dry_run: bool) -> dict:
    """symbol_catalog 非法行删除 + 残留格式合并。表不存在时跳过（惰性创建）。"""
    stats = {"invalid_del": 0, "conflict_del": 0, "merged_upd": 0, "dirty_sym": 0, "skipped": False}
    cur = db.cursor()
    exists = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_catalog'"
    ).fetchone()
    if not exists:
        print("[symbol_catalog] 表不存在（惰性创建，尚未写入），跳过")
        stats["skipped"] = True
        return stats
    rows = cur.execute(
        "SELECT DISTINCT exchange, symbol FROM symbol_catalog ORDER BY exchange, symbol"
    ).fetchall()
    invalid: list[tuple[str, str]] = []
    targets: list[tuple[str, str, str]] = []
    for ex, sym in rows:
        if sym is None:
            continue
        s = str(sym).strip()
        if not s:
            continue
        if not is_valid_base_symbol(s):
            invalid.append((ex, s))
            continue
        base = normalize_symbol(s)
        if base and base != s:
            targets.append((ex, s, base))
    stats["dirty_sym"] = len(targets)
    if invalid:
        print(f"[symbol_catalog] 非法 symbol {len(invalid)} 组: {invalid[:10]}{'...' if len(invalid) > 10 else ''}")
    for ex, s in invalid:
        if not dry_run:
            cur.execute("DELETE FROM symbol_catalog WHERE exchange=? AND symbol=?", (ex, s))
        stats["invalid_del"] += 1
    for ex, dirty, base in targets:
        if not dry_run:
            # 撞唯一约束 (exchange, symbol)：先删 base 已存在时的残留行，再更新
            cur.execute(
                "DELETE FROM symbol_catalog WHERE exchange=? AND symbol=? "
                "AND EXISTS (SELECT 1 FROM symbol_catalog WHERE exchange=? AND symbol=?)",
                (ex, dirty, ex, base),
            )
            stats["conflict_del"] += cur.rowcount
            cur.execute(
                "UPDATE symbol_catalog SET symbol=? WHERE exchange=? AND symbol=?",
                (base, ex, dirty),
            )
            stats["merged_upd"] += cur.rowcount
        else:
            n_conflict = cur.execute(
                "SELECT COUNT(*) FROM symbol_catalog WHERE exchange=? AND symbol=? "
                "AND EXISTS (SELECT 1 FROM symbol_catalog WHERE exchange=? AND symbol=?)",
                (ex, dirty, ex, base),
            ).fetchone()[0]
            stats["conflict_del"] += n_conflict
            stats["merged_upd"] += 1 - n_conflict
    if not dry_run:
        db.commit()
    return stats


def verify(db: sqlite3.Connection) -> dict:
    """清洗后验证：残留 symbol 与非法 symbol 应归零。"""
    cur = db.cursor()
    out: dict = {}
    krows = cur.execute(
        "SELECT DISTINCT exchange, symbol FROM crypto_klines"
    ).fetchall()
    k_dirty = [
        (ex, s) for ex, s in krows
        if s and (not is_valid_base_symbol(s) or normalize_symbol(s) != str(s).strip())
    ]
    out["klines_remaining_dirty"] = len(k_dirty)
    cat_exists = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_catalog'"
    ).fetchone()
    if cat_exists:
        crows = cur.execute(
            "SELECT DISTINCT exchange, symbol FROM symbol_catalog"
        ).fetchall()
        c_dirty = [
            (ex, s) for ex, s in crows
            if s and (not is_valid_base_symbol(s) or normalize_symbol(s) != str(s).strip())
        ]
        out["catalog_remaining_dirty"] = len(c_dirty)
    else:
        out["catalog_remaining_dirty"] = 0
    # 重复条目（同一 exchange+symbol 组内 period+timestamp 冲突数）
    out["klines_dup_pairs"] = cur.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT exchange, symbol, period, timestamp, COUNT(*) c
            FROM crypto_klines GROUP BY exchange, symbol, period, timestamp HAVING c > 1
        )
        """
    ).fetchone()[0]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="crypto_klines / symbol_catalog symbol 去重归一化清洗")
    ap.add_argument("--dry-run", action="store_true", help="只统计不执行")
    args = ap.parse_args()
    dry = args.dry_run

    if not MARKET_DB.exists():
        print(f"FATAL: market db 不存在 {MARKET_DB}")
        return 1

    print(f"[cleanup] {'DRY-RUN（不执行写入）' if dry else '执行'} db={MARKET_DB}")
    backup_db(dry)

    db = sqlite3.connect(str(MARKET_DB), timeout=60)
    try:
        t0 = time.time()
        s1 = cleanup_klines(db, dry)
        print(f"[crypto_klines] {s1}")
        s2 = cleanup_catalog(db, dry)
        print(f"[symbol_catalog] {s2}")
        if not dry:
            # 触发 checkpoint，尽快归还 WAL 空间
            try:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
        v = verify(db)
        print(f"[verify] {v}")
        print(f"[cleanup] 耗时 {time.time() - t0:.1f}s，{'dry-run' if dry else '完成'}")
        if not dry and (v["klines_remaining_dirty"] or v["catalog_remaining_dirty"] or v["klines_dup_pairs"]):
            print("[cleanup] WARN: 仍有残留，可重跑本脚本（幂等）")
    except Exception as exc:
        print(f"[cleanup] 失败: {exc}", file=sys.stderr)
        db.rollback()
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
