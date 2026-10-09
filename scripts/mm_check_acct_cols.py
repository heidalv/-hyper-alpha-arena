import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal

with system_identity():
    with SessionLocal() as db:
        cols = db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='arbitrage_paper_accounts' ORDER BY ordinal_position"
        )).scalars().all()
        print("arbitrage_paper_accounts 列:", cols)
        cols2 = db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='arbitrage_paper_exchange_balances' ORDER BY ordinal_position"
        )).scalars().all()
        print("arbitrage_paper_exchange_balances 列:", cols2)
        # 当前账户行状态（内存已清零、DB 未动 → 应仍是旧值）
        r = db.execute(text(
            "SELECT id, total_equity, available_balance, frozen_balance, realized_pnl "
            "FROM arbitrage_paper_accounts WHERE id=101"
        )).mappings().first()
        print("当前账户行:", dict(r))
