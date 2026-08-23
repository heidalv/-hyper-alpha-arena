# -*- coding: utf-8 -*-
"""扫描：DB 有 tenant_id 列但 ORM 模型未声明的表（RLS 写入断裂隐患）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
import backend.database.models as _models
from sqlalchemy.orm import class_mapper
import inspect

_env = {}
for _line in Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        _env[_k] = _v

# ORM: tablename -> 是否有 tenant_id 属性
orm = {}
for name, obj in inspect.getmembers(_models, inspect.isclass):
    if not hasattr(obj, "__tablename__"):
        continue
    try:
        mapper = class_mapper(obj)
    except Exception:
        continue
    has_tid = "tenant_id" in {c.key for c in mapper.column_attrs}
    orm[obj.__tablename__] = has_tid

eng = create_engine(_env["DATABASE_URL"], pool_pre_ping=True)
with eng.connect() as c:
    c.execute(text("SET app.is_admin='on'"))
    c.execute(text("SET app.tenant_id=326"))
    rows = c.execute(text(
        "SELECT DISTINCT table_name FROM information_schema.columns "
        "WHERE table_schema='public' AND column_name='tenant_id' "
        "ORDER BY table_name"
    )).fetchall()

missing = []
for (t,) in rows:
    if orm.get(t) is False:
        missing.append(t)
    elif t not in orm:
        missing.append(t + " (ORM无模型)")

print("DB 有 tenant_id 但 ORM 未映射的表:")
for m in missing:
    print(" -", m)
print(f"\n共 {len(missing)} 个")
