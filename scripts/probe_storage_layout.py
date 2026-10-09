# -*- coding: utf-8 -*-
"""落盘位置与磁盘余量核查（只读）：确认"项目所有数据和数据库在 D 盘"，并评估扩覆盖的磁盘影响。"""
from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("=" * 96)
print("① 仓库与数据目录落盘位置")
print("=" * 96)
for label, p in (("repo root", ROOT), ("data/", ROOT / "data"), ("logs/", ROOT / "logs")):
    try:
        ex = p.exists()
        free = shutil.disk_usage(p if ex else p.parent)
        print(f"  {label:12s} {str(p):60s} 存在={ex}  盘={str(p)[:2]}  "
              f"该盘余量={free.free/1e9:.1f}GB / {free.total/1e9:.1f}GB")
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:12s} {p}  <{type(exc).__name__}: {exc}>")

print()
print("=" * 96)
print("② PostgreSQL 实例的真实数据目录 / 配置文件")
print("=" * 96)
for drv in ("C", "D"):
    free = shutil.disk_usage(f"{drv}:\\")
    print(f"  {drv}: 余量 {free.free/1e9:8.1f} GB / 总 {free.total/1e9:8.1f} GB "
          f"（已用 {(free.total-free.free)/free.total:.1%}）")

try:
    from sqlalchemy import text
    from backend.database.connection import MarketSessionLocal as S
    db = S()
    try:
        db.execute(text("SET app.is_admin='on'"))
        for k in ("data_directory", "config_file", "hba_file", "server_version"):
            try:
                v = db.execute(text(f"SHOW {k}")).fetchone()[0]
                print(f"  {k:18s} = {v}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {k:18s} <{type(exc).__name__}>")
        print()
        print("  各库大小：")
        for r in db.execute(text(
            "SELECT datname, pg_size_pretty(pg_database_size(datname)) "
            "FROM pg_database WHERE datistemplate = false ORDER BY pg_database_size(datname) DESC"
        )).fetchall():
            print(f"    {str(r[0]):22s} {r[1]}")
        print()
        print("  alpha_market 里最大的 6 张表：")
        for r in db.execute(text("""
            SELECT relname, pg_size_pretty(pg_total_relation_size(relid)), n_live_tup
            FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 6
        """)).fetchall():
            print(f"    {str(r[0]):28s} {str(r[1]):>10s}  行≈{r[2]:,}")
        print()
        r = db.execute(text(
            "SELECT count(*), pg_size_pretty(pg_total_relation_size('crypto_klines')) "
            "FROM crypto_klines")).fetchone()
        print(f"  crypto_klines: 行数={r[0]:,}  总大小={r[1]}")
        print()
        print("  每个 (period) 的行数与占用（评估扩宇宙的边际成本）：")
        for r in db.execute(text("""
            SELECT period, count(*) AS n FROM crypto_klines GROUP BY period ORDER BY n DESC
        """)).fetchall():
            print(f"    {str(r[0]):6s} {r[1]:>12,} 行")
    finally:
        db.close()
except Exception as exc:  # noqa: BLE001
    print(f"  （DB 查询失败: {type(exc).__name__}: {str(exc)[:120]}）")

print()
print("=" * 96)
print("③ C 盘上是否有本项目的残留数据目录（用户提示后主动排查）")
print("=" * 96)
cands = [
    r"C:\Program Files\PostgreSQL",
    r"C:\Program Files (x86)\PostgreSQL",
    r"C:\PostgreSQL",
    r"C:\pgsql",
    str(Path.home() / "AppData" / "Local" / "postgresql"),
    r"C:\alpha",
    r"C:\001Alpha",
]
for c in cands:
    p = Path(c)
    if p.exists():
        try:
            sz = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e9
            print(f"  存在: {c}  大小≈{sz:.2f} GB")
        except Exception:  # noqa: BLE001
            print(f"  存在: {c}  （大小不可算）")
    else:
        print(f"  无:   {c}")
print()
print("  仓库里的 .bat 提示（历史搬迁脚本）：")
for b in ("start-pg-on-d.bat", "switch-postgres-to-d.bat", "switch-postgres-to-c.bat",
          "start-pg-d-fixync-once.bat", "migrate-pg-live-to-d.bat"):
    f = ROOT / b
    print(f"    {b:32s} {'存在' if f.exists() else '无'}")
