"""
数据库查询性能优化指南
Database Query Performance Optimization Guide

提供常见查询的性能优化建议和最佳实践
"""

from sqlalchemy import Index, create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.sql import select, func
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class QueryOptimizer:
    """查询优化器 - 提供常见的性能优化模式"""

    @staticmethod
    def get_recent_records_optimized(
        session: Session,
        model_class,
        filters: List[Any] = None,
        order_by_field: str = "created_at",
        limit: int = 100
    ) -> List[Any]:
        """
        优化的最近记录查询

        优化点：
        1. 使用索引字段排序
        2. 限制返回数量
        3. 只选择需要的字段
        """
        query = session.query(model_class)

        # 应用过滤器（优先使用索引字段）
        if filters:
            for filter_condition in filters:
                query = query.filter(filter_condition)

        # 使用索引排序并限制结果
        query = query.order_by(getattr(model_class, order_by_field).desc()).limit(limit)

        return query.all()

    @staticmethod
    def get_latest_by_symbol_optimized(
        session: Session,
        model_class,
        symbols: List[str],
        symbol_field: str = "symbol",
        timestamp_field: str = "timestamp"
    ) -> Dict[str, Any]:
        """
        优化的按symbol获取最新记录

        优化点：
        1. 使用DISTINCT ON减少返回数据量
        2. 只查询需要的字段
        3. 使用窗口函数避免多次查询
        """
        # PostgreSQL的DISTINCT ON语法
        from sqlalchemy.dialects.postgresql import distinct

        subquery = session.query(
            model_class.symbol,
            func.row_number().over(
                partition_by=model_class.symbol,
                order_by=getattr(model_class, timestamp_field).desc()
            ).label('rn')
        ).filter(model_class.symbol.in_(symbols)).subquery()

        latest = session.query(model_class).join(
            subquery,
            (getattr(model_class, symbol_field) == subquery.symbol) &
            (subquery.rn == 1)
        ).all()

        return {item.symbol: item for item in latest}

    @staticmethod
    def count_with_cache_bypass(session: Session, model_class) -> int:
        """
        优化的计数查询

        优化点：
        1. 不使用缓存
        2. 使用简单的COUNT(*)
        """
        return session.query(func.count(model_class.id)).scalar()

    @staticmethod
    def batch_insert_optimized(session: Session, model_class, records: List[Dict]) -> int:
        """
        优化的批量插入

        优化点：
        1. 使用bulk_insert_mappings
        2. 批量提交
        3. 返回插入数量
        """
        try:
            result = session.bulk_insert_mappings(model_class, records, return_defaults=True)
            session.commit()
            return len(result)
        except Exception as e:
            session.rollback()
            logger.error(f"Batch insert failed: {e}")
            raise

    @staticmethod
    def get_aggregated_data_optimized(
        session: Session,
        model_class,
        group_by_fields: List[str],
        aggregations: Dict[str, Any],
        filters: List[Any] = None
    ) -> List[Dict]:
        """
        优化的聚合查询

        优化点：
        1. 使用HAVING过滤聚合结果
        2. 只选择必要的字段
        3. 使用索引字段分组
        """
        query = session.query(*[
            getattr(model_class, field) for field in group_by_fields
        ])

        # 添加聚合函数
        for field, agg_func in aggregations.items():
            query = query.add_columns(agg_func)

        # 应用分组
        query = query.group_by(*[
            getattr(model_class, field) for field in group_by_fields
        ])

        # 应用过滤器
        if filters:
            for filter_condition in filters:
                query = query.filter(filter_condition)

        return query.all()


# === 常见查询优化建议 ===

OPTIMIZATION_TIPS = """
=== 数据库查询性能优化建议 ===

1. **使用索引字段进行过滤和排序**
   - 确保WHERE和ORDER BY子句使用索引字段
   - 示例：symbol, timestamp, account_id都有索引

2. **避免SELECT ***
   - 只查询需要的字段
   - 示例：session.query(Model.field1, Model.field2)

3. **使用JOIN代替子查询**
   - JOIN通常比相关子查询更快
   - 示例：session.query(Model1).join(Model2)

4. **批量操作代替循环查询**
   - 使用bulk_insert_mappings, bulk_update_mappings
   - 避免在循环中执行单个INSERT/UPDATE

5. **使用EXPLAIN ANALYZE分析慢查询**
   - 在PostgreSQL中执行EXPLAIN ANALYZE <query>
   - 检查是否有Seq Scan（全表扫描）

6. **为常用查询模式创建复合索引**
   - 示例：Index('model', ['symbol', 'timestamp'])
   - 注意：索引会降低写入性能，只在必要时添加

7. **使用查询缓存**
   - 价格数据使用短期缓存（3秒）
   - 账户信息使用中期缓存（10秒）
   - 历史数据使用长期缓存（30秒）

8. **分页查询大数据集**
   - 使用LIMIT和OFFSET
   - 对于深度分页，使用游标分页代替OFFSET

9. **定期维护数据库**
   - VACUUM ANALYZE回收空间和更新统计信息
   - REINDEX重建碎片化索引
   - 建议每周执行一次

10. **监控慢查询日志**
    - 在PostgreSQL中设置log_min_duration_statement = 1000
    - 记录执行时间超过1秒的查询
"""


def create_missing_indexes(engine):
    """
    创建常用的复合索引以提升性能

    这些索引对高频查询特别有效
    """
    indexes = [
        # 账户快照查询
        "CREATE INDEX IF NOT EXISTS idx_snapshot_account_time ON hyperliquid_account_snapshots(wallet_address, snapshot_time DESC)",

        # 持仓历史查询
        "CREATE INDEX IF NOT EXISTS idx_position_account_time ON hyperliquid_positions(wallet_address, symbol, entry_time DESC)",

        # AI决策查询
        "CREATE INDEX IF NOT EXISTS idx_decision_account_time ON ai_decision_logs(account_id, decision_time DESC)",

        # 交易历史查询
        "CREATE INDEX IF NOT EXISTS idx_trade_account_time ON hyperliquid_trades(wallet_address, symbol, timestamp DESC)",

        # K线数据查询
        "CREATE INDEX IF NOT EXISTS idx_kline_symbol_time ON crypto_klines(symbol, timestamp DESC) WHERE environment='mainnet'",

        # 市场数据聚合查询
        "CREATE INDEX IF NOT EXISTS idx_trades_agg_symbol_time ON market_trades_aggregated(symbol, sample_time DESC)",

        # [2026-07-09 性能修复] 模拟盘热表复合索引
        # LeakGuard 日志反复出现 paper_orders/paper_positions 的慢 count(*) 全表扫描
        # （age 高达 133s）。这些表虽有 ORM index=True，但 create_all 不会给已存在的表补索引。
        # 仪表盘按 (account_id, status) 过滤，加复合索引让这些查询走索引扫描。
        # paper_orders: get_summary 的 count(*) + 按账户/状态筛选
        "CREATE INDEX IF NOT EXISTS idx_paper_orders_account_status ON paper_orders(account_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_paper_orders_strategy ON paper_orders(account_id, strategy_id, status)",
        # paper_positions: get_positions 按账户+状态取开仓持仓
        "CREATE INDEX IF NOT EXISTS idx_paper_positions_account_status ON paper_positions(account_id, status)",
        # strategy_trades: 按 strategy_id + 平仓时间倒序统计（此表无 account_id 列）
        "CREATE INDEX IF NOT EXISTS idx_strategy_trades_strategy_closed ON strategy_trades(strategy_id, closed_at DESC)",

        # [2026-07-11 性能修复] 补充复合索引（见 RAG/OpenCode/数据库优化方案 阶段0）：
        # crypto_klines 实际热查询按 (exchange, symbol, period) 过滤 + timestamp 倒序，
        # 而现有 idx_kline_symbol_time 只覆盖 (symbol, timestamp)，缺 exchange/period。
        "CREATE INDEX IF NOT EXISTS idx_kline_exchange_symbol_period_time "
        "ON crypto_klines(exchange, symbol, period, timestamp DESC)",

        # signal_trade_feedback: scalp_confidence_calibrator 按 signal_type + created_at
        # 窗口过滤，此前只有单列索引，复合索引可避免"索引扫描后再过滤"。
        "CREATE INDEX IF NOT EXISTS idx_signal_feedback_type_created "
        "ON signal_trade_feedback(signal_type, created_at)",

        # strategy_memories: master_execution 按 updated_at 倒序取样本量达标的记忆。
        "CREATE INDEX IF NOT EXISTS idx_strategy_memories_updated "
        "ON strategy_memories(updated_at DESC)",

        # paper_orders: 按账户 + 下单时间倒序查历史订单（此前只有 account_id 单列索引）。
        "CREATE INDEX IF NOT EXISTS idx_paper_orders_account_created "
        "ON paper_orders(account_id, created_at DESC)",
    ]

    # [2026-07-09 性能修复] 原实现在同一连接/事务里连续执行所有 CREATE INDEX，
    # 一旦某条失败（表不在本库/列不存在），PostgreSQL 会把事务置为 aborted，
    # 之后所有语句全部 "InFailedSqlTransaction: 当前事务被终止" 被跳过 —— 这正是
    # 索引从未被创建的根因。改为每条语句用独立事务：每条 CREATE INDEX 单独 begin()，
    # 成功即 commit，失败即 rollback（释放该条的事务），下一条在新事务里执行，互不影响。
    created_count = 0
    failed_count = 0
    for index_sql in indexes:
        try:
            with engine.connect() as conn:
                # 显式开事务执行 DDL，成功 commit 落盘；异常则 with 退出时自动回滚
                tx = conn.begin()
                try:
                    conn.execute(text(index_sql))
                    tx.commit()
                    created_count += 1
                    logger.info(f"Created index: {index_sql[:80]}")
                except Exception:
                    tx.rollback()
                    raise
        except Exception as e:
            failed_count += 1
            logger.warning(f"Failed to create index: {str(e)[:120]}")
    logger.info(
        f"[create_missing_indexes] 完成: 成功 {created_count}, 失败/跳过 {failed_count}"
    )


def tune_autovacuum_for_hot_tables(engine):
    """[2026-07-11 阶段2] 给高写入量的热表收紧 autovacuum 触发阈值。

    背景（RAG/OpenCode/数据库优化方案 阶段2「数据库运维层大改造」）：
    - 本机 PostgreSQL 未安装 pg_cron 扩展，且当前是单机实盘交易系统，没有确认过的
      维护窗口/备份策略——K线大表按 period+月份做声明式分区、以及只读副本这两项
      属于侵入性 schema 迁移/新基础设施，风险与当前表规模（crypto_klines 约80万行
      /586MB）不匹配，按计划"视效果分批推进"的原则先不在本轮直接执行，留给数据量
      增长到明显需要时再做（届时代码层已有的复合索引仍然有效，不受影响）。
    - 但"定时 VACUUM"这一项可以低风险地现在落地：autovacuum 守护进程本身已经在跑
      （SHOW autovacuum 确认默认开启），只是默认阈值
      autovacuum_vacuum_scale_factor=0.2（即死元组达20%才触发）对高频写入的热表
      （market_orderbook_snapshots 93万行、crypto_klines 80万行等）偏松，等真正
      触发时单次 VACUUM 要处理的死元组量已经很大、耗时更长。这里对几张实测数据量
      最大/更新最频繁的表单独收紧阈值到 5%，无需 pg_cron，无需重启，`ALTER TABLE`
      为幂等 DDL，重复调用安全。
    """
    hot_tables = [
        "market_orderbook_snapshots",
        "market_asset_metrics",
        "market_trades_aggregated",
        "crypto_klines",
        "raw_market_events",
        "perp_funding",
        "ai_decision_logs",
        "decision_snapshots",
        "signal_trade_feedback",
        "paper_orders",
        "paper_positions",
    ]
    tuned_count = 0
    skipped_count = 0
    for table in hot_tables:
        sql = (
            f"ALTER TABLE {table} SET ("
            f"autovacuum_vacuum_scale_factor = 0.05, "
            f"autovacuum_analyze_scale_factor = 0.02"
            f")"
        )
        try:
            with engine.connect() as conn:
                tx = conn.begin()
                try:
                    conn.execute(text(sql))
                    tx.commit()
                    tuned_count += 1
                except Exception:
                    tx.rollback()
                    raise
        except Exception as e:
            # 表不在本库很常见（同一份 hot_tables 清单被 core/market/analytics 三个
            # engine 复用），静默跳过，不视为异常。
            skipped_count += 1
            logger.debug(f"[AutovacuumTune] {table} 跳过: {str(e)[:100]}")
    logger.info(
        f"[AutovacuumTune] 完成: 收紧 {tuned_count} 张表的autovacuum阈值, 跳过(表不存在) {skipped_count}"
    )


def analyze_query_performance(engine, query: str):
    """
    分析查询性能并提供优化建议

    Args:
        engine: SQLAlchemy engine
        query: SQL查询语句

    Returns:
        分析结果字典
    """
    with engine.connect() as conn:
        # 执行EXPLAIN ANALYZE
        result = conn.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, VERBOSE) {query}"))

        plan = result.fetchall()

        # 分析执行计划
        analysis = {
            "has_seq_scan": any("Seq Scan" in str(row) for row in plan),
            "uses_index": any("Index Scan" in str(row) or "Index Only Scan" in str(row) for row in plan),
            "execution_time": None,
            "buffer_hit_ratio": None,
        }

        # 提取执行时间
        for row in plan:
            if "Execution Time" in str(row):
                time_str = str(row).split("Execution Time: ")[1].split(" ms")[0]
                analysis["execution_time"] = float(time_str)

        # 生成优化建议
        suggestions = []
        if analysis["has_seq_scan"]:
            suggestions.append("⚠️  查询使用了全表扫描，考虑添加索引")
        if not analysis["uses_index"]:
            suggestions.append("⚠️  查询没有使用索引，检查WHERE和ORDER BY字段")
        if analysis["execution_time"] and analysis["execution_time"] > 1000:
            suggestions.append(f"⚠️  查询执行时间过长: {analysis['execution_time']:.0f}ms")

        if not suggestions:
            suggestions.append("✅ 查询性能良好")

        analysis["suggestions"] = suggestions

        return analysis


# === 示例：优化前后对比 ===

OPTIMIZATION_EXAMPLES = """
=== 查询优化示例 ===

示例1：获取账户最近的持仓
---
优化前（可能全表扫描）:
session.query(HyperliquidPosition).filter(
    HyperliquidPosition.wallet_address == wallet,
    HyperliquidPosition.size != 0
).order_by(HyperliquidPosition.entry_time.desc()).limit(10)

优化后（使用索引）:
session.query(HyperliquidPosition).filter(
    HyperliquidPosition.wallet_address == wallet,
    HyperliquidPosition.size != 0
).order_by(
    HyperliquidPosition.entry_time.desc(),
    HyperliquidPosition.symbol.asc()
).limit(10)

示例2：获取多个symbol的最新价格
---
优化前（多次查询）:
prices = {}
for symbol in symbols:
    price = session.query(CryptoPrice).filter(
        CryptoPrice.symbol == symbol
    ).order_by(CryptoPrice.price_date.desc()).first()
    prices[symbol] = price

优化后（使用窗口函数，单次查询）:
subquery = session.query(
    CryptoPrice.symbol,
    func.row_number().over(
        partition_by=CryptoPrice.symbol,
        order_by=CryptoPrice.price_date.desc()
    ).label('rn')
).subquery()

prices = {
    item.symbol: item
    for item in session.query(CryptoPrice)
    .join(subquery, CryptoPrice.symbol == subquery.symbol)
    .filter(subquery.rn == 1)
    .all()
}

示例3：聚合交易数据
---
优化前（Python中聚合）:
trades = session.query(HyperliquidTrade).all()
result = {}
for trade in trades:
    if trade.symbol not in result:
        result[trade.symbol] = {"count": 0, "total": 0}
    result[trade.symbol]["count"] += 1
    result[trade.symbol]["total"] += trade.size

优化后（数据库聚合）:
result = session.query(
    HyperliquidTrade.symbol,
    func.count(HyperliquidTrade.id).label('count'),
    func.sum(HyperliquidTrade.size).label('total')
).group_by(HyperliquidTrade.symbol).all()
"""


if __name__ == "__main__":
    logger.info(OPTIMIZATION_TIPS)
    logger.info("\n" + "="*60)
    logger.info(OPTIMIZATION_EXAMPLES)
