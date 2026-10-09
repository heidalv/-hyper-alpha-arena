# -*- coding: utf-8 -*-
"""核实：① C 盘 PG 目录是否为 junction（纠正我 105.6GB 的误读）；② D 盘余量下扩覆盖的边际成本。只读。"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("=" * 92)
print("① 目录是否为 junction/符号链接（决定我的 105.6GB 是不是跨盘重复计数）")
print("=" * 92)
for d in (r"C:\Program Files\PostgreSQL", r"C:\Program Files\PostgreSQL\15",
          r"C:\Program Files\PostgreSQL\15\data", r"D:\PostgreSQL\15\data"):
    p = Path(d)
    if not p.exists():
        print(f"  {d:52s} 不存在")
        continue
    try:
        # 不用 rglob（会跟 junction 跨盘）——只看这一层与 reparse 属性
        st = os.stat(d, follow_symlinks=False)
        is_reparse = bool(getattr(st, "st_file_attributes", 0) & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
        link = os.path.islink(d)
        try:
            n_files = len(os.listdir(d))
        except Exception:  # noqa: BLE001
            n_files = -1
        print(f"  {d:52s} 目录项={n_files:5d}  reparse={is_reparse}  islink={link}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {d:52s} <{type(exc).__name__}: {exc}>")

print("\n  —— dir 命令看 junction 标记（<JUNCTION> / <SYMLINKD>）——")
for d in (r"C:\Program Files\PostgreSQL\15\data", r"C:\Program Files\PostgreSQL\15"):
    try:
        out = subprocess.run(["cmd", "/c", "dir", "/AL", str(Path(d).parent)],
                             capture_output=True, text=True, timeout=20,
                             encoding="utf-8", errors="replace")
        lines = [ln for ln in (out.stdout or "").splitlines() if "JUNCTION" in ln or "SYMLINK" in ln]
        print(f"  {Path(d).parent}: " + ("; ".join(lines) if lines else "（无 junction/symlink 条目）"))
    except Exception as exc:  # noqa: BLE001
        print(f"  <{type(exc).__name__}>")

print()
print("=" * 92)
print("② D 盘余量下的容量评估（扩覆盖的边际成本）")
print("=" * 92)
import shutil  # noqa: E402
du = shutil.disk_usage("D:\\")
print(f"  D: 余量 {du.free/1e9:.2f} GB / {du.total/1e9:.2f} GB（已用 {(du.total-du.free)/du.total:.1%}）")
dp = Path(r"D:\PostgreSQL\15\data")
ddu = shutil.disk_usage(str(dp))
print(f"  PG 数据目录所在盘同盘 ⇒ PG 增长直接吃这 {ddu.free/1e9:.2f} GB")

try:
    from sqlalchemy import text
    from backend.database.connection import MarketSessionLocal as S

    def q(sql, **kw):
        db = S()
        try:
            db.execute(text("SET app.is_admin='on'"))
            r = db.execute(text(sql), kw).fetchall()
            db.rollback()
            return r
        except Exception as exc:  # noqa: BLE001
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            return [("ERR", f"{type(exc).__name__}: {str(exc)[:90]}")]
        finally:
            db.close()

    print("\n  [DB] 各库大小：")
    for r in q("SELECT datname, pg_size_pretty(pg_database_size(datname)) FROM pg_database "
               "WHERE datistemplate = false ORDER BY pg_database_size(datname) DESC"):
        print("    ", tuple(r))

    print("\n  [DB] alpha_market 最大的 6 张表：")
    for r in q("""SELECT relname, pg_size_pretty(pg_total_relation_size(relid)), n_live_tup
                  FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 6"""):
        print("    ", tuple(r))

    print("\n  [DB] crypto_klines 行数与各周期（评估边际写入量）：")
    for r in q("SELECT count(*) FROM crypto_klines"):
        print("     总行数 =", r[0])
    for r in q("SELECT period, count(*) FROM crypto_klines GROUP BY period ORDER BY 2 DESC"):
        print(f"     {str(r[0]):6s} {r[1]:>12,}")
    for r in q("SELECT pg_size_pretty(pg_total_relation_size('crypto_klines'))"):
        print("     表总大小 =", r[0])

    print("\n  [DB] 每个标的的 K 线行数（Top10 与最少 5 个）：")
    for r in q("SELECT symbol, count(*) c FROM crypto_klines GROUP BY symbol ORDER BY c DESC LIMIT 10"):
        print(f"     {str(r[0]):12s} {r[1]:>10,}")
    for r in q("SELECT symbol, count(*) c FROM crypto_klines GROUP BY symbol ORDER BY c ASC LIMIT 5"):
        print(f"     (少) {str(r[0]):8s} {r[1]:>10,}")
except Exception as exc:  # noqa: BLE001
    print(f"  （DB 查询失败: {type(exc).__name__}: {str(exc)[:120]}）")
