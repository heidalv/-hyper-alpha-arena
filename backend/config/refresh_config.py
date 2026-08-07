"""
统一数据刷新频率配置
Unified Data Refresh Configuration

所有数据源的刷新间隔都在这里定义，确保前后端一致
"""

from typing import Dict
from dataclasses import dataclass

@dataclass
class RefreshConfig:
    """数据刷新配置"""
    interval_seconds: int
    description: str

# === 统一刷新配置 (单位: 秒) ===
# 优化后策略：价格3秒，账户30秒，AI/分析60秒，图表30秒

REFRESH_CONFIGS: Dict[str, RefreshConfig] = {
    # === 实时价格数据 ===
    "price_ticker": RefreshConfig(
        interval_seconds=3,
        description="实时价格刷新间隔 - 用于价格行情显示"
    ),

    # === 账户余额数据 ===
    "binance_balance": RefreshConfig(
        interval_seconds=30,
        description="币安余额刷新间隔"
    ),
    "hyperliquid_balance": RefreshConfig(
        interval_seconds=30,
        description="Hyperliquid余额刷新间隔"
    ),

    # === 持仓数据 ===
    "binance_positions": RefreshConfig(
        interval_seconds=30,
        description="币安持仓刷新间隔"
    ),
    "hyperliquid_positions": RefreshConfig(
        interval_seconds=30,
        description="Hyperliquid持仓刷新间隔"
    ),

    # === WebSocket快照推送 ===
    "websocket_snapshot": RefreshConfig(
        interval_seconds=3,
        description="WebSocket账户快照推送间隔"
    ),

    # === 交易历史 ===
    "recent_trades": RefreshConfig(
        interval_seconds=30,
        description="最近交易历史刷新间隔"
    ),
    "action_summary": RefreshConfig(
        interval_seconds=60,
        description="交易行为汇总刷新间隔"
    ),

    # === AI决策和分析 ===
    "ai_decisions": RefreshConfig(
        interval_seconds=60,
        description="AI决策历史刷新间隔"
    ),
    "attribution_analysis": RefreshConfig(
        interval_seconds=60,
        description="归因分析数据刷新间隔"
    ),

    # === K线图表 ===
    "kline_chart": RefreshConfig(
        interval_seconds=3,
        description="K线图表数据刷新间隔"
    ),

    # === 市场流向数据 ===
    "market_flow": RefreshConfig(
        interval_seconds=30,
        description="市场流向指标刷新间隔 (CVD, OI_DELTA等)"
    ),
}

# === 价格缓存配置 ===
class PriceCacheConfig:
    """价格缓存TTL配置"""

    # 短期缓存：用于实时显示
    SHORT_TTL = 2  # 秒 - 价格行情使用

    # 中期缓存：用于计算和查询
    MEDIUM_TTL = 10  # 秒 - 一般查询使用

    # 长期缓存：用于历史数据
    LONG_TTL = 30  # 秒 - 历史快照使用

# === 前端显示配置 ===
class FrontendDisplayConfig:
    """前端显示相关配置"""

    # 刷新状态显示的格式
    REFRESH_DISPLAY_FORMAT = "每 {seconds} 秒自动刷新"

    # 刷新按钮的loading状态时间
    LOADING_TIMEOUT = 2  # 秒 - 超过这个时间显示loading

    # 数据延迟提示阈值
    DELAY_WARNING_THRESHOLD = 15  # 秒 - 超过这个时间显示"数据可能延迟"

# === 获取配置的辅助函数 ===
def get_refresh_interval(data_type: str) -> int:
    """获取指定数据类型的刷新间隔"""
    config = REFRESH_CONFIGS.get(data_type)
    if not config:
        raise ValueError(f"Unknown data type: {data_type}. Available: {list(REFRESH_CONFIGS.keys())}")
    return config.interval_seconds

def get_all_intervals() -> Dict[str, int]:
    """获取所有刷新间隔的字典"""
    return {k: v.interval_seconds for k, v in REFRESH_CONFIGS.items()}

# === 配置验证 ===
def validate_config():
    """验证配置的合理性"""
    issues = []

    # 检查是否有冲突的配置
    price_related = [
        ("price_ticker", get_refresh_interval("price_ticker")),
        ("binance_balance", get_refresh_interval("binance_balance")),
        ("binance_positions", get_refresh_interval("binance_positions")),
    ]

    if price_related[0][1] > price_related[1][1]:
        issues.append(
            f"价格刷新({price_related[0][1]}s)应该快于余额刷新({price_related[1][1]}s)"
        )

    # 检查缓存TTL是否合理
    if PriceCacheConfig.SHORT_TTL > get_refresh_interval("price_ticker"):
        issues.append(
            f"价格缓存TTL({PriceCacheConfig.SHORT_TTL}s)不应该大于价格刷新间隔({get_refresh_interval('price_ticker')}s)"
        )

    return issues

# 运行验证
if __name__ == "__main__":
    issues = validate_config()
    if issues:
        print("⚠️  配置问题:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("✅ 配置验证通过")

    print("\n=== 当前刷新配置 ===")
    for name, config in REFRESH_CONFIGS.items():
        print(f"{name:30s}: {config.interval_seconds:3d}s - {config.description}")
