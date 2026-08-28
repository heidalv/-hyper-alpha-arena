#!/usr/bin/env python3
"""
Migration: exchange_credentials 增加 proxy_url 列（凭证级代理出口）。

[2026-08-28] 币安 API 需 IP 白名单，不同凭证可能走不同代理 IP。
空 = 用环境变量 BINANCE_HTTPS_PROXY/HTTPS_PROXY。
幂等：列已存在则跳过。
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine, inspect, text
from backend.database.connection import DATABASE_URL


def upgrade():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        cols = [c["name"] for c in inspect(conn).get_columns("exchange_credentials")]
        if "proxy_url" in cols:
            print("proxy_url already exists, skip")
            return
        conn.execute(text(
            "ALTER TABLE exchange_credentials ADD COLUMN proxy_url VARCHAR(512)"
        ))
        conn.commit()
        print("added exchange_credentials.proxy_url")


if __name__ == "__main__":
    upgrade()
