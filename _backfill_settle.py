"""一次性回填 scalp_signal_log 的结算积压。

背景：结算定时任务随 register_evolution_tasks() 于 2026-07-30 停用而失效，
导致 07-30 12:26 之后的信号只入库、未回填输赢。本脚本补齐这段积压。

用法：python _backfill_settle.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
# 积压跨约 25 小时，加大回看根数确保取得到结算价（500 根 5m ≈ 41.7h）
os.environ.setdefault("SCALP_SETTLE_KLINE_LOOKBACK", "800")

from backend.services.scalp_signal_logger import settle_pending  # noqa: E402


def pending_count() -> int:
    from backend.database.connection import SessionLocal
    from backend.database.models import ScalpSignalLog
    db = SessionLocal()
    try:
        return (db.query(ScalpSignalLog)
                .filter(ScalpSignalLog.settled == False)  # noqa: E712
                .count())
    finally:
        db.close()


def retry_no_price(since: str = "2026-07-30") -> int:
    """把因取价失败被放弃的信号重置为未结算，供修正取价源后重试。

    no_price 行不含任何结算信息，重置不会丢数据。
    """
    from sqlalchemy import text
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        res = db.execute(text(
            "update scalp_signal_log set settled=false, settle_note=null "
            "where settle_note='no_price' and created_at >= :since"
        ), {"since": since})
        db.commit()
        return int(res.rowcount or 0)
    finally:
        db.close()


def main():
    if "--retry-no-price" in sys.argv:
        n = retry_no_price()
        print(f"已重置 {n} 条 no_price 信号，准备用修正后的取价源重试")

    start = pending_count()
    print(f"开始回填，待结算 {start} 条")
    total_settled = total_skipped = 0
    for rnd in range(1, 41):
        st = settle_pending(limit=1000)
        total_settled += st.get("settled", 0)
        total_skipped += st.get("skipped", 0)
        left = pending_count()
        print(f"  第{rnd}轮: 检查{st.get('checked',0)} 结算{st.get('settled',0)} "
              f"放弃{st.get('skipped',0)} 剩余{left}")
        # 本轮既没结算也没放弃 → 剩下的都是未到期，停止
        if st.get("settled", 0) == 0 and st.get("skipped", 0) == 0:
            break
    print(f"\n完成：累计结算 {total_settled} 条，放弃 {total_skipped} 条，"
          f"剩余待结算 {pending_count()} 条（未到期属正常）")


if __name__ == "__main__":
    main()
