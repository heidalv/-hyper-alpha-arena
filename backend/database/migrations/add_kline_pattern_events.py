"""
K线形态事件记录表 — 存储检测到的形态及其信号结果
"""

from backend.database.migration_base import Migration


class AddKlinePatternEventsTable(Migration):
    """创建 kline_pattern_events 表，记录检测到的 K 线形态事件"""

    name = "add_kline_pattern_events_table"
    description = "存储算法检测到的 K 线形态事件，跟踪形态出现后的价格走势"

    def up(self, db):
        """创建表"""
        db.execute("""
            CREATE TABLE IF NOT EXISTS kline_pattern_events (
                id BIGSERIAL PRIMARY KEY,
                exchange VARCHAR(32) NOT NULL,
                symbol VARCHAR(32) NOT NULL,
                period VARCHAR(8) NOT NULL,
                pattern_id VARCHAR(64) NOT NULL,
                pattern_name VARCHAR(128) NOT NULL,
                pattern_type VARCHAR(16) NOT NULL CHECK (pattern_type IN ('bullish', 'bearish', 'neutral')),
                event_timestamp BIGINT NOT NULL,
                signal_price DOUBLE PRECISION,
                confidence DOUBLE PRECISION DEFAULT 0.5,
                description TEXT,
                trading_hints JSONB DEFAULT '[]',
                reliability VARCHAR(16) DEFAULT 'medium',

                -- 后续验证字段
                verified BOOLEAN DEFAULT FALSE,
                price_1h DOUBLE PRECISION,
                price_4h DOUBLE PRECISION,
                price_24h DOUBLE PRECISION,
                result_type VARCHAR(16),

                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 索引
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_pattern_events_symbol_time
                ON kline_pattern_events (exchange, symbol, period, event_timestamp DESC)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_pattern_events_type
                ON kline_pattern_events (pattern_type, event_timestamp DESC)
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_pattern_events_verified
                ON kline_pattern_events (verified, created_at DESC)
        """)
        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_events_unique
                ON kline_pattern_events (exchange, symbol, period, pattern_id, event_timestamp)
        """)

    def down(self, db):
        """删除表"""
        db.execute("DROP TABLE IF EXISTS kline_pattern_events CASCADE")
