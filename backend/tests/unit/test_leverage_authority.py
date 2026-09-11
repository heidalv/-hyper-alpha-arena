# backend/tests/unit/test_leverage_authority.py
import pytest

def test_leverage_cap_by_tier():
    from backend.services.leverage_authority import resolve_leverage
    # long tier 限 12,short/mid 限 20(单一权威)
    assert resolve_leverage(tier="long", requested=20.0) == 12
    assert resolve_leverage(tier="short", requested=10.0) == 10.0
    assert resolve_leverage(tier="mid", requested=25.0) == 20  # 钳到 cap

def test_consecutive_loss_lowers_cap():
    from backend.services.leverage_authority import resolve_leverage
    # 连亏时 mental_state 下调 cap,respect 之(不重置)
    assert resolve_leverage(tier="long", requested=12.0, mental_cap=5) == 5

def test_no_tier_floors_at_1():
    from backend.services.leverage_authority import resolve_leverage
    assert resolve_leverage(tier=None, requested=0.0) == 1.0

def test_mental_cap_cannot_exceed_tier_cap():
    """mental_cap 即便很高,也不能超过 tier cap。"""
    from backend.services.leverage_authority import resolve_leverage
    # long cap=12,mental_cap=15 → 仍受 tier cap 限,且 requested=12
    assert resolve_leverage(tier="long", requested=12.0, mental_cap=15) == 12


# ─────────── [2026-09-04] 币种杠杆：交易所同币一仓一杠杆，周期不参与分配 ───────────

@pytest.fixture
def _clean_lev_env(monkeypatch):
    """清掉 .env 注入的覆盖，用内置档位表跑断言。"""
    monkeypatch.delenv("SYMBOL_LEVERAGE_MAP", raising=False)
    monkeypatch.delenv("SYMBOL_LEVERAGE_DEFAULT", raising=False)
    monkeypatch.setenv("SYMBOL_LEVERAGE_ENABLED", "true")


@pytest.mark.parametrize("raw,expect", [
    ("BTC", "BTC"), ("btc", "BTC"), ("BTC/USDT:USDT", "BTC"), ("BTCUSDT", "BTC"),
    ("ETH-USDT", "ETH"), ("ASTER/USDT:USDT", "ASTER"), ("USDT", "USDT"), ("", ""),
])
def test_normalize_base_symbol(raw, expect):
    """各交易所写法归一到裸基币名；"USDT" 自身不能被削成空串。"""
    from backend.services.leverage_authority import normalize_base_symbol
    assert normalize_base_symbol(raw) == expect


def test_symbol_leverage_tiers(_clean_lev_env):
    from backend.services.leverage_authority import symbol_leverage
    assert symbol_leverage("BTC") == 5.0
    assert symbol_leverage("ETH/USDT:USDT") == 5.0
    assert symbol_leverage("SOL") == 4.0
    assert symbol_leverage("ASTER") == 3.0      # 未列入 → fallback
    assert symbol_leverage("没听过的币") == 3.0


def test_symbol_overrides_requested(_clean_lev_env):
    """核心契约：传了 symbol 时上游请求的杠杆一律失效。

    交易所按币种设杠杆、同币同向仓位合并，"长线3x/短线10x"落不了地。
    """
    from backend.services.leverage_authority import resolve_leverage
    assert resolve_leverage(tier="short", requested=20.0, symbol="BTC") == 5.0
    assert resolve_leverage(tier="long", requested=10.0, symbol="BTC") == 5.0
    # 同一个币，周期不同也必须是同一个值
    assert (resolve_leverage(tier="short", requested=20.0, symbol="ASTER")
            == resolve_leverage(tier="long", requested=1.0, symbol="ASTER") == 3.0)


def test_symbol_leverage_respects_risk_caps(_clean_lev_env):
    """币种档位是设定值，但风控收紧项依然能压低它。"""
    from backend.services.leverage_authority import resolve_leverage
    assert resolve_leverage(tier="long", requested=10.0, symbol="BTC", mental_cap=2.0) == 2.0

    class _Acct:
        tier_overrides = {"long": {"leverage": 2}}

    assert resolve_leverage(tier="long", requested=10.0, symbol="BTC", account=_Acct()) == 2.0


def test_symbol_leverage_map_override(monkeypatch):
    from backend.services.leverage_authority import symbol_leverage
    monkeypatch.setenv("SYMBOL_LEVERAGE_ENABLED", "true")
    monkeypatch.setenv("SYMBOL_LEVERAGE_MAP", "BTC:2,DOGE:7")
    monkeypatch.setenv("SYMBOL_LEVERAGE_DEFAULT", "1.5")
    assert symbol_leverage("BTC") == 2.0        # 覆盖内置 5x
    assert symbol_leverage("DOGE") == 7.0
    assert symbol_leverage("ASTER") == 1.5      # 覆盖 fallback
    # 非法项跳过，不炸
    monkeypatch.setenv("SYMBOL_LEVERAGE_MAP", "BTC:abc,,ETH:,SOL:4")
    assert symbol_leverage("SOL") == 4.0
    assert symbol_leverage("BTC") == 5.0        # 非法 → 回内置表


def test_symbol_leverage_disabled_falls_back(monkeypatch):
    """开关关闭 → 回到旧的 requested 行为（应急回滚路径）。"""
    from backend.services.leverage_authority import resolve_leverage
    monkeypatch.setenv("SYMBOL_LEVERAGE_ENABLED", "false")
    assert resolve_leverage(tier="long", requested=10.0, symbol="BTC") == 10.0


def test_position_construction_uses_symbol_leverage(_clean_lev_env):
    """PC：杠杆取币种档位；份额只由名义决定，与杠杆无关。"""
    from backend.services.position_construction import construct
    btc = construct(lane="long", symbol="BTC", equity=5000.0, price=100.0,
                    base_weight=0.2, realized_vol=0.8, stop_distance_pct=0.08)
    aster = construct(lane="long", symbol="ASTER", equity=5000.0, price=100.0,
                      base_weight=0.2, realized_vol=0.8, stop_distance_pct=0.08)
    assert btc.leverage == 5.0 and aster.leverage == 3.0
    # 同权重同价 → 名义/数量必须一致，只有占用的保证金不同
    assert abs(btc.notional - aster.notional) < 1e-6
    assert abs(btc.quantity - aster.quantity) < 1e-9
    assert btc.margin < aster.margin
    assert abs(btc.margin - btc.notional / 5.0) < 1e-6


def test_clamp_passes_leverage_through(_clean_lev_env):
    """clamp 不得二次决策杠杆（否则会改掉 adopt 来的存量仓杠杆），仅兜底异常高倍。"""
    from backend.services.position_construction import clamp
    r = clamp(lane="long", symbol="BTC", equity=5000.0, price=100.0,
              notional=1000.0, leverage=5.0)
    assert r.leverage == 5.0
    # 存量 10x 仓 adopt 后透传，不被改成 5x
    r10 = clamp(lane="long", symbol="BTC", equity=5000.0, price=100.0,
                notional=1000.0, leverage=10.0)
    assert r10.leverage == 10.0
    # 配置写错的异常高倍被兜底夹住
    r50 = clamp(lane="long", symbol="BTC", equity=5000.0, price=100.0,
                notional=1000.0, leverage=50.0)
    assert r50.leverage == 10.0
    assert any("leverage_guard" in c for c in r50.caps_applied)
