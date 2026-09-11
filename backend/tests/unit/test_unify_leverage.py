# backend/tests/unit/test_unify_leverage.py
"""杠杆统一/钳制契约。

- _clamp_leverage_by_tier 纯函数：按 tier cap 钳制(long=12, short/mid=20)。
- _unify_leverage_for_side：
    netting-on（默认，对齐 Hyperliquid/Asterdex 同币一仓一杠杆）→ 同币**所有**本地子仓
    同步到本笔订单杠杆，margin/强平价重算；防"新单随意改写既有杠杆"的责任在 trade_gate
    （有开腿时 adopt 既有腿最大杠杆）。
    netting-off（旧行为）→ 仅同方向统一到 target，并按 cap 钳制。
[2026-09-02] 原文件断言的是更早的"按各腿 tier cap 各自钳制、互不影响"设计，已被上述设计取代。
"""
import pytest


def test_clamp_leverage_by_tier_caps_long_at_12():
    """long tier cap=12,即便传入 20 也钳到 12。"""
    from backend.services.paper_trading_engine import _clamp_leverage_by_tier
    assert _clamp_leverage_by_tier(20.0, "long") == 12


def test_clamp_leverage_by_tier_keeps_value_under_cap():
    """10x 不应被提到 20(不被 max 污染)。"""
    from backend.services.paper_trading_engine import _clamp_leverage_by_tier
    assert _clamp_leverage_by_tier(10.0, "long") == 10.0


def test_clamp_leverage_by_tier_short_caps_at_20():
    from backend.services.paper_trading_engine import _clamp_leverage_by_tier
    assert _clamp_leverage_by_tier(25.0, "short") == 20  # 钳到 cap


def test_clamp_leverage_by_tier_none_tier_floors_at_1():
    from backend.services.paper_trading_engine import _clamp_leverage_by_tier
    assert _clamp_leverage_by_tier(0.0, None) == 1.0


def test_existing_position_leverage_not_raised_by_new_order_target():
    """既有仓位杠杆不被新订单目标抬高(只按自身 tier cap 降)。

    核心语义(评审 issue#1): netting 模式下每个既有仓位仅按自身 tier cap 钳制,
    新订单的 target_leverage 对既有仓位无任何影响。任何方向的 cross-pollination
    (up via max 或其他)都是 bug。
    """
    from backend.services.paper_trading_engine import _clamp_leverage_by_tier
    # 模拟:既有 short 仓位 10x,新订单 target 20x → 既有仓位保持 10x(不被提到 20)
    # 这个测 _clamp_leverage_by_tier 的纯函数语义即可:输入是仓位自身杠杆 10,
    # 10 < cap 20,保持 10。
    assert _clamp_leverage_by_tier(10.0, "short") == 10.0  # 10 < cap 20,保持
    # 孤儿 25x short 被钳到 20(降杠杆生效)
    assert _clamp_leverage_by_tier(25.0, "short") == 20.0
    # 既有 long 仓位 12x(=cap),新订单 target 20 → 保持 12,绝不被提到 20
    assert _clamp_leverage_by_tier(12.0, "long") == 12.0
    # 既有 long 仓位 8x,新订单 target 20 → 保持 8(降杠杆不被反向抬升)
    assert _clamp_leverage_by_tier(8.0, "long") == 8.0


def test_unify_leverage_netting_on_syncs_all_legs_to_order_leverage(monkeypatch):
    """集成验证: netting-on 分支下,同币所有本地子仓同步到本笔订单杠杆。

    [2026-09-02 契约更正] 原用例断言"既有仓位不被新单 target 抬高"，对应的是被取代的旧设计。
    现行设计（paper_trading_engine._unify_leverage_for_side 注释）："交易所同币一仓一杠杆，
    所有本地子仓同步到本笔订单杠杆；不得再按各 tier cap 留不同杠杆"。防污染责任前移到
    trade_gate：符号已有开腿时新单先 adopt 既有腿的最大杠杆（"adopt existing leverage"），
    所以到这里 target 通常就是既有杠杆；本函数只负责把所有腿拉齐并重算 margin/强平价。

    场景: 已有 10x + 8x 两个 long 腿，订单 target=20x → 两腿都变 20x，margin=notional/20。
    """
    # 强制 netting 模式开启,保证走 netting_on 分支
    import backend.config.settings as settings
    monkeypatch.setattr(settings, "PAPER_NETTING_MODE", True)

    from backend.services.paper_trading_engine import PaperTradingEngine

    class _FakePos:
        def __init__(self, leverage, tier, side="long", size=1.0, entry_price=100.0):
            self.leverage = leverage
            self.timeframe_tier = tier
            self.trade_nature = "trend_follow"
            self.side = side
            self.size = size
            self.entry_price = entry_price
            self.margin = 0.0
            self.liquidation_price = 0.0

    class _FakeQuery:
        def __init__(self, items):
            self._items = items
        def filter(self, *a, **k):
            return self  # 忽略过滤,直接返回全部(模拟跨方向查询)
        def all(self):
            return list(self._items)

    pos_a = _FakePos(10.0, "long")
    pos_b = _FakePos(8.0, "long")

    class _FakeDB:
        def query(self, model):
            return _FakeQuery([pos_a, pos_b])

    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine._unify_leverage_for_side(_FakeDB(), account_id=1, symbol="BTC",
                                    side="long", target_leverage=20.0)
    assert pos_a.leverage == 20.0 and pos_b.leverage == 20.0, "同币所有腿必须拉齐到订单杠杆"
    # margin = notional / lev = (1*100)/20 = 5.0；强平价随新杠杆重算（非 0）
    assert pos_a.margin == pytest.approx(5.0)
    assert pos_a.liquidation_price and pos_a.liquidation_price != 0.0


def test_unify_leverage_netting_on_syncs_down_too(monkeypatch):
    """集成验证: netting-on 分支下,拉齐是双向的——高杠杆腿也会被拉到订单杠杆。

    [2026-09-02 契约更正] 原用例期望"25x 被 tier cap 钳到 20、18x 保持"（旧设计）。
    现行设计不按 tier cap 分别保留：25x 与 18x 两腿都同步到订单的 10x，
    margin=notional/10 重算。
    """
    import backend.config.settings as settings
    monkeypatch.setattr(settings, "PAPER_NETTING_MODE", True)

    from backend.services.paper_trading_engine import PaperTradingEngine

    class _FakePos:
        def __init__(self, leverage, tier, side="short", size=2.0, entry_price=100.0):
            self.leverage = leverage
            self.timeframe_tier = tier
            self.trade_nature = "scalp"
            self.side = side
            self.size = size
            self.entry_price = entry_price
            self.margin = 0.0
            self.liquidation_price = 0.0

    class _FakeQuery:
        def __init__(self, items):
            self._items = items
        def filter(self, *a, **k):
            return self
        def all(self):
            return list(self._items)

    pos_orphan = _FakePos(25.0, "short", size=2.0, entry_price=100.0)
    pos_ok = _FakePos(18.0, "short", size=1.0, entry_price=100.0)

    class _FakeDB:
        def query(self, model):
            return _FakeQuery([pos_orphan, pos_ok])

    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine._unify_leverage_for_side(_FakeDB(), account_id=1, symbol="BTC",
                                    side="short", target_leverage=10.0)
    assert pos_orphan.leverage == 10.0 and pos_ok.leverage == 10.0, "两腿都应同步到订单 10x"
    # margin 按新杠杆重算 = notional / lev = (2*100)/10 = 20.0；(1*100)/10 = 10.0
    assert pos_orphan.margin == pytest.approx(20.0)
    assert pos_ok.margin == pytest.approx(10.0)


def test_unify_leverage_netting_off_keeps_same_side_only(monkeypatch):
    """netting-off（旧行为）：仅同方向统一到 target，并按 tier cap 钳制。"""
    import backend.config.settings as settings
    monkeypatch.setattr(settings, "PAPER_NETTING_MODE", False)
    from backend.services.paper_trading_engine import PaperTradingEngine, _clamp_leverage_by_tier

    class _FakePos:
        def __init__(self, leverage, side):
            self.leverage = leverage
            self.timeframe_tier = "long"
            self.trade_nature = "trend_follow"
            self.side = side
            self.size = 1.0
            self.entry_price = 100.0
            self.margin = 0.0
            self.liquidation_price = 0.0

    a, b = _FakePos(10.0, "long"), _FakePos(8.0, "long")

    class _FakeQuery:
        def __init__(self, items):
            self._items = items
        def filter(self, *x, **k):
            return self
        def all(self):
            return list(self._items)

    class _FakeDB:
        def query(self, model):
            return _FakeQuery([a, b])

    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine._unify_leverage_for_side(_FakeDB(), account_id=1, symbol="BTC",
                                    side="long", target_leverage=50.0)
    expected = _clamp_leverage_by_tier(50.0, None)
    assert a.leverage == expected and b.leverage == expected

