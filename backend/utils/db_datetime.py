"""PostgreSQL TIMESTAMP（无时区）读写约定。

数据库 session 时区为 Asia/Shanghai。TIMESTAMP 列里的 naive 时间
表示北京时间（无论来自 current_timestamp()，还是 psycopg 将 aware UTC
写入时的本地转换）。API 统一序列化为 UTC ISO，前端再转本地显示。

## 读侧铁律（轮71 补）

凡是要拿 TIMESTAMP 列的值**与 now 相减**（算年龄/时长/冷却/间隔），
必须经 `db_dt_for_age()`（= `parse_db_naive_to_utc`）归一化，
**不得**用 `dt.replace(tzinfo=timezone.utc)` —— 那会把本地钟面当 UTC，
差 8 小时且判定方向可能反转（见 `db_dt_for_age` 的实测说明）。
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


def db_dt_for_age(dt) -> Optional[datetime]:
    """读取 TIMESTAMP 列并用于**与 now 相减**时的唯一正确姿势。

    ## 为什么每个「算年龄/时长」的读取点都必须用它

    `paper_positions` 等表的 `TIMESTAMP`（无时区）列里存的是**北京时间钟面**：
    写入侧普遍用 `datetime.now(timezone.utc)`（aware），psycopg 交给 PG 后
    按会话时区 Asia/Shanghai 落库，于是列里是「本地钟面」。

    读取侧若把读回的 naive 值当成 UTC（`dt.replace(tzinfo=timezone.utc)`），
    再与 `datetime.now(timezone.utc)` 相减，就会**差 8 小时**。实测：

        写入 datetime.now(timezone.utc) = 12:54:45+00:00
        读回                            = 20:54:45（本地钟面）
        旧算法 elapsed = (utcnow - 读回.replace(tzinfo=utc))/3600 = **-8.00h**

    后果不是「数值略偏」，而是**判定反向**：减仓冷却把刚减过仓的仓位判成
    「距上次 -8.0h < 1.0h」→ 全部拒绝；持仓时长（age/hold）变成负数。

    本函数即 `parse_db_naive_to_utc` 的语义化别名，把「这是给年龄计算用的」
    写进调用点，避免再次被误当作 UTC。
    """
    return parse_db_naive_to_utc(dt)
