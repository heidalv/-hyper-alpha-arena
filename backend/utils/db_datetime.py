"""PostgreSQL TIMESTAMP（无时区）读写约定。

数据库 session 时区为 Asia/Shanghai。TIMESTAMP 列里的 naive 时间
表示北京时间（无论来自 current_timestamp()，还是 psycopg 将 aware UTC
写入时的本地转换）。API 统一序列化为 UTC ISO，前端再转本地显示。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# 与 PostgreSQL SHOW timezone 保持一致
DB_NAIVE_TZ = timezone(timedelta(hours=8))


def db_naive_to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """将 DB 读出的 datetime 序列化为 UTC ISO 字符串。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DB_NAIVE_TZ)
    return dt.astimezone(timezone.utc).isoformat()


def utc_now_for_db() -> datetime:
    """写入 TIMESTAMP 列：存 UTC 钟面值的 naive datetime（避免 PG 时区二次转换）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_db_naive_to_utc(dt) -> Optional[datetime]:
    """将 DB/API 时间解析为 UTC aware datetime。

    - naive datetime → 按北京时间理解
    - 带 Z / offset 的 ISO 字符串 → 标准解析
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        text = dt.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=DB_NAIVE_TZ)
        return parsed.astimezone(timezone.utc)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=DB_NAIVE_TZ).astimezone(timezone.utc)
        return dt.astimezone(timezone.utc)
    return None
