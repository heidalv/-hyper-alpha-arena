import sys
sys.path.insert(0, r'D:\001Alpha\Hyper-Alpha-Arena')
from dotenv import load_dotenv
load_dotenv(r'D:\001Alpha\Hyper-Alpha-Arena\.env', override=True)
from sqlalchemy import create_engine, text

eng = create_engine('postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena')
with eng.connect() as c:
    # 所有连接的详细信息，按 xact_start 排序
    rows = c.execute(text("""
        SELECT pid, application_name, state, xact_start,
               now() - coalesce(xact_start, query_start) AS txn_age,
               backend_xid IS NOT NULL AS has_write_txn,
               left(query, 120) AS q
        FROM pg_stat_activity
        WHERE datname = 'alpha_arena'
        ORDER BY xact_start NULLS LAST
    """)).fetchall()
    print('=== alpha_arena connections ===')
    for r in rows:
        print(f'pid={r[0]} app={r[1]} state={r[2]} xact_start={r[3]} age={r[4]} has_write={r[5]} q={r[6]}')

    # 表级锁
    print('=== locks on full_auto_sessions ===')
    rows = c.execute(text("""
        SELECT pid, locktype, mode, granted
        FROM pg_locks WHERE relation IN (
            SELECT oid FROM pg_class WHERE relname='full_auto_sessions'
        )
    """)).fetchall()
    for r in rows:
        print(r)

    # 看未提交事务中有多少行
    print('=== 尝试读取未提交事务的行（隔离级别会拦截，这里仅看 blocked 情况） ===')
    try:
        # 在 READ UNCOMMITTED 下 postgres 实际上不读未提交，但能看到是否被锁阻塞
        c.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        cnt = c.execute(text("SELECT count(*) FROM full_auto_sessions")).scalar()
        print('count=', cnt)
    except Exception as e:
        print('err:', e)
