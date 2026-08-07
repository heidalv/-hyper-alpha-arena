"""
钉钉推送系统数据库迁移
创建时间: 2026-01-11
功能: 添加钉钉机器人推送相关表
"""

def upgrade(db_session):
    """创建钉钉推送相关表"""

    print("开始创建钉钉推送表...")

    # 创建 dingtalk_bots 表
    print("1. 创建 dingtalk_bots 表...")
    db_session.execute("""
        CREATE TABLE IF NOT EXISTS dingtalk_bots (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            webhook_url TEXT NOT NULL,
            sign_secret VARCHAR(255),
            enabled BOOLEAN DEFAULT TRUE,

            notify_on_position_opened BOOLEAN DEFAULT TRUE,
            notify_on_position_closed BOOLEAN DEFAULT TRUE,
            notify_on_stop_loss_triggered BOOLEAN DEFAULT TRUE,
            notify_on_take_profit_triggered BOOLEAN DEFAULT TRUE,
            notify_on_position_scheduled BOOLEAN DEFAULT FALSE,

            position_schedule_interval INTEGER DEFAULT 3600,
            max_notifications_per_hour INTEGER DEFAULT 20,

            volatility_alert_enabled BOOLEAN DEFAULT FALSE,
            volatility_threshold DECIMAL(5,2) DEFAULT 5.0,
            volatility_timeframe INTEGER DEFAULT 300,

            account_ids TEXT,
            symbol_filter TEXT,

            total_sent_count INTEGER DEFAULT 0,
            last_sent_at TIMESTAMP,
            last_error_at TIMESTAMP,
            last_error_message TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 创建 dingtalk_notifications 表
    print("2. 创建 dingtalk_notifications 表...")
    db_session.execute("""
        CREATE TABLE IF NOT EXISTS dingtalk_notifications (
            id SERIAL PRIMARY KEY,
            bot_id INTEGER REFERENCES dingtalk_bots(id) ON DELETE CASCADE,
            account_id INTEGER REFERENCES accounts(id),

            event_type VARCHAR(50) NOT NULL,
            message_type VARCHAR(20) DEFAULT 'text',
            title VARCHAR(200),
            content TEXT NOT NULL,
            raw_data JSONB,

            status VARCHAR(20) DEFAULT 'pending',
            dingtalk_msg_id VARCHAR(100),

            response_code INTEGER,
            response_body TEXT,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP,

            position_id VARCHAR(100),
            order_id VARCHAR(100),
            symbol VARCHAR(50)
        )
    """)

    # 创建 dingtalk_notification_stats 表
    print("3. 创建 dingtalk_notification_stats 表...")
    db_session.execute("""
        CREATE TABLE IF NOT EXISTS dingtalk_notification_stats (
            id SERIAL PRIMARY KEY,
            bot_id INTEGER REFERENCES dingtalk_bots(id) ON DELETE CASCADE,
            date DATE NOT NULL,

            total_sent INTEGER DEFAULT 0,
            total_success INTEGER DEFAULT 0,
            total_failed INTEGER DEFAULT 0,

            event_breakdown JSONB,
            avg_response_time_ms INTEGER,
            max_response_time_ms INTEGER,
            error_breakdown JSONB,

            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(bot_id, date)
        )
    """)

    # 创建索引
    print("4. 创建索引...")
    db_session.execute("CREATE INDEX IF NOT EXISTS idx_dingtalk_bots_enabled ON dingtalk_bots(enabled)")

    db_session.execute("CREATE INDEX IF NOT EXISTS idx_dingtalk_notifications_bot ON dingtalk_notifications(bot_id)")
    db_session.execute("CREATE INDEX IF NOT EXISTS idx_dingtalk_notifications_account ON dingtalk_notifications(account_id)")
    db_session.execute("CREATE INDEX IF NOT EXISTS idx_dingtalk_notifications_event ON dingtalk_notifications(event_type)")
    db_session.execute("CREATE INDEX IF NOT EXISTS idx_dingtalk_notifications_status ON dingtalk_notifications(status)")
    db_session.execute("CREATE INDEX IF NOT EXISTS idx_dingtalk_notifications_created ON dingtalk_notifications(created_at DESC)")

    db_session.execute("CREATE INDEX IF NOT EXISTS idx_dingtalk_stats_bot_date ON dingtalk_notification_stats(bot_id, date DESC)")

    db_session.commit()
    print("✅ 钉钉推送表创建完成")


def downgrade(db_session):
    """回滚迁移"""
    print("开始回滚钉钉推送表...")
    db_session.execute("DROP TABLE IF EXISTS dingtalk_notification_stats")
    db_session.execute("DROP TABLE IF EXISTS dingtalk_notifications")
    db_session.execute("DROP TABLE IF EXISTS dingtalk_bots")
    db_session.commit()
    print("✅ 钉钉推送表删除完成")


if __name__ == "__main__":
    # 测试运行
    from backend.database.connection import get_db_session

    db = get_db_session()
    try:
        upgrade(db)
        print("\n测试查询:")
        result = db.execute("SELECT COUNT(*) as count FROM dingtalk_bots")
        print(f"dingtalk_bots 表行数: {result.fetchone()[0]}")
    except Exception as e:
        print(f"❌ 错误: {e}")
        db.rollback()
    finally:
        db.close()
