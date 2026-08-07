"""费率中心化服务测试。

验证 fee_schedule_service 作为单一费率真相源:
- per-exchange 维持保证金率（asterdex/hl=0.005, binance=0.004）
- per-exchange maker/taker 手续费率
- 交易所名归一化（别名解析）
- 兼容层 engine_maint_margin_rate
- 全局覆盖 settings.MAINT_MARGIN_RATIO
"""
import pytest

from backend.services.fee_schedule_service import (
    canonical_exchange,
    engine_maint_margin_rate,
    get_all_exchange_rules,
    get_all_exchange_summary,
    get_exchange_rules,
    get_fee_rate,
    get_maint_margin_rate,
    get_min_notional,
    get_quantity_step,
)


# ── 交易所名归一化 ──────────────────────────────────────────────

def test_canonical_exchange_aliases():
    assert canonical_exchange("hl") == "hyperliquid"
    assert canonical_exchange("hyper") == "hyperliquid"
    assert canonical_exchange("aster") == "asterdex"
    assert canonical_exchange("aster_dex") == "asterdex"
    assert canonical_exchange("binanceusdm") == "binance"
    assert canonical_exchange("gate") == "gateio"


def test_canonical_exchange_none_uses_default():
    assert canonical_exchange(None) == "asterdex"
    assert canonical_exchange("") == "asterdex"


def test_canonical_exchange_case_insensitive():
    assert canonical_exchange("Hyperliquid") == "hyperliquid"
    assert canonical_exchange("ASTERDEX") == "asterdex"
    assert canonical_exchange("Binance") == "binance"


def test_canonical_exchange_unknown_falls_back():
    # 未知交易所降级到默认（不报错）
    assert canonical_exchange("unknown_exchange") == "asterdex"


# ── 维持保证金率 ────────────────────────────────────────────────

def test_maint_margin_rate_asterdex():
    assert get_maint_margin_rate("asterdex") == 0.005


def test_maint_margin_rate_binance_lower():
    # binance 维持保证金率 0.004（低于其他交易所 0.005）
    assert get_maint_margin_rate("binance") == 0.004


def test_maint_margin_rate_hyperliquid():
    assert get_maint_margin_rate("hyperliquid") == 0.005


def test_maint_margin_rate_okx_bybit_gateio():
    for ex in ("okx", "bybit", "gateio"):
        assert get_maint_margin_rate(ex) == 0.005


def test_maint_margin_rate_none_uses_global():
    # None 时用全局 settings.MAINT_MARGIN_RATIO（默认 0.005）
    mmr = get_maint_margin_rate(None)
    assert mmr == 0.005  # 默认全局值


# ── 手续费率 ────────────────────────────────────────────────────

def test_fee_rate_asterdex_maker_taker_equal():
    # asterdex maker == taker == 0.00005（返利交易所）
    assert get_fee_rate("asterdex", is_maker=True) == 0.00005
    assert get_fee_rate("asterdex", is_maker=False) == 0.00005


def test_fee_rate_hyperliquid():
    assert get_fee_rate("hyperliquid", is_maker=True) == 0.0002
    assert get_fee_rate("hyperliquid", is_maker=False) == 0.00035


def test_fee_rate_binance_taker_higher_than_maker():
    maker = get_fee_rate("binance", is_maker=True)
    taker = get_fee_rate("binance", is_maker=False)
    assert maker == 0.0002
    assert taker == 0.0004
    assert taker > maker


def test_fee_rate_asterdex_cheapest():
    # asterdex 是最便宜的（返利生态）
    asterdex_fee = get_fee_rate("asterdex", is_maker=False)
    for ex in ("hyperliquid", "binance", "okx", "bybit", "gateio"):
        assert get_fee_rate(ex, is_maker=False) > asterdex_fee


# ── 完整规则对象 ────────────────────────────────────────────────

def test_get_exchange_rules_asterdex():
    rules = get_exchange_rules("asterdex")
    assert rules.exchange == "asterdex"
    assert rules.maker_fee_rate == 0.00005
    assert rules.taker_fee_rate == 0.00005
    assert rules.min_notional_usd == 5.0
    assert rules.maintenance_margin_rate == 0.005


def test_get_min_notional():
    assert get_min_notional("hyperliquid") == 10.0
    assert get_min_notional("asterdex") == 5.0
    assert get_min_notional("binance") == 5.0


def test_get_quantity_step():
    assert get_quantity_step("asterdex") == 0.0001


# ── 兼容层 ──────────────────────────────────────────────────────

def test_engine_maint_margin_rate_compatible():
    # engine_maint_margin_rate(None) 应与 get_maint_margin_rate(None) 一致
    assert engine_maint_margin_rate(None) == get_maint_margin_rate(None)
    # 指定交易所时返回 per-exchange 值
    assert engine_maint_margin_rate("binance") == 0.004
    assert engine_maint_margin_rate("asterdex") == 0.005


# ── 批量视图 ────────────────────────────────────────────────────

def test_get_all_exchange_rules_count():
    rules = get_all_exchange_rules()
    assert len(rules) == 6  # hyperliquid/asterdex/binance/okx/bybit/gateio
    assert "asterdex" in rules
    assert "hyperliquid" in rules


def test_get_all_exchange_summary_structure():
    summary = get_all_exchange_summary()
    assert len(summary) == 6
    item = summary[0]
    assert "exchange" in item
    assert "maker_fee_rate" in item
    assert "taker_fee_rate" in item
    assert "maker_fee_pct" in item
    assert "maintenance_margin_rate" in item
    assert "min_notional_usd" in item


# ── 单一真相源验证（不重复定义）────────────────────────────────

def test_fee_schedule_consistent_with_simulator():
    """验证 fee_schedule_service 与 paper_exchange_simulator 表一致（单一真相源）。"""
    from backend.services.exchange.paper_exchange_simulator import (
        DEFAULT_EXCHANGE_RULES,
    )
    for name, sim_rules in DEFAULT_EXCHANGE_RULES.items():
        svc_rules = get_exchange_rules(name)
        assert svc_rules.maker_fee_rate == sim_rules.maker_fee_rate
        assert svc_rules.taker_fee_rate == sim_rules.taker_fee_rate
        assert svc_rules.maintenance_margin_rate == sim_rules.maintenance_margin_rate
        assert svc_rules.min_notional_usd == sim_rules.min_notional_usd
