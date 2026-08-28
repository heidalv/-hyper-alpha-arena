#!/usr/bin/env python3
"""Migration: exchange_proxy_configs 表(交易所API IP白名单代理出口) + 种子数据。
[2026-08-28] 幂等: 表存在则跳过;种子按 proxy_url 去重。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine, text
from backend.database.connection import DATABASE_URL


def upgrade():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        exists = conn.execute(text(
            "SELECT to_regclass('public.exchange_proxy_configs')"
        )).scalar()
        if exists:
            print("table exists")
        else:
            conn.execute(text("""
                CREATE TABLE exchange_proxy_configs (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    proxy_url VARCHAR(512) NOT NULL,
                    egress_ip VARCHAR(64),
                    note VARCHAR(255) DEFAULT '',
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
            print("created exchange_proxy_configs")
        # 种子: 本机链式代理(币安白名单出口 8.211.172.14)
        dup = conn.execute(text(
            "SELECT 1 FROM exchange_proxy_configs WHERE proxy_url='http://127.0.0.1:18080'"
        )).scalar()
        if not dup:
            conn.execute(text("""
                INSERT INTO exchange_proxy_configs (name, proxy_url, egress_ip, note)
                VALUES ('币安链式代理', 'http://127.0.0.1:18080', '8.211.172.14',
                        '本机链式出口 18080→SS 1080→8.211.172.14:55620(SOCKS5认证)')
            """))
            conn.commit()
            print("seeded 币安链式代理")


if __name__ == "__main__":
    upgrade()
