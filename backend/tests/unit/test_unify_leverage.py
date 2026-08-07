# backend/tests/unit/test_unify_leverage.py
"""根因 2 止血测试:杠杆按 tier cap 钳制,不再被历史仓位 max 污染。

相关修复:
- paper_trading_engine._unify_leverage_for_side 不再用 max 覆盖所有同币种仓位
- add-path (DCA/add 合并) 不再用 max 提杠杆
- 新增 _clamp_leverage_by_tier 纯函数,按 tier cap 钳制(long=12, short/mid=20)
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


def test_unify_leverage_netting_on_does_not_raise_existing_via_target(monkeypatch):
    """集成验证: netting-on 分支下,新订单 target_leverage 不抬高既有仓位杠杆。

    场景: 已有两个 long 仓位: 10x (tier=long) + 8x (tier=long)。新订单 target=20x。
    终态: 两仓均保持自身杠杆(自身 < cap 12,无变化),绝不被提到 20。
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
    # 既有 10x 仓位: 自身 < cap(12),不应被新订单 target=20 抬高
    assert pos_a.leverage == 10.0, f"既有仓位不应被新订单 target 抬高,实际 {pos_a.leverage}"
    assert pos_b.leverage == 8.0, f"既有仓位不应被新订单 target 抬高,实际 {pos_b.leverage}"


def test_unify_leverage_netting_on_lowers_via_tier_cap(monkeypatch):
    """集成验证: netting-on 分支下,孤儿超 cap 仓位被自身 tier cap 钳制(降杠杆)。

    场景: 已有两个 short 仓位: 25x (tier=short, cap=20) + 18x (tier=short)。
    新订单 target=10x。终态: 孤儿 25x 被钳到 20(降杠杆生效),18x 保持,
    margin/liquidation 同步重算。两仓均不受 target=10 或任何 max 抬升影响。
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
    # target=10x 不影响既有仓位;既有仓位 25x 自身被 cap(20)钳到 20
    engine._unify_leverage_for_side(_FakeDB(), account_id=1, symbol="BTC",
                                    side="short", target_leverage=10.0)
    assert pos_orphan.leverage == 20.0, (
        f"孤儿 25x 应被自身 tier cap 降到 20,实际 {pos_orphan.leverage}")
    # margin 按新杠杆重算 = notional / lev = (2*100)/20 = 10.0
    assert pos_orphan.margin == pytest.approx(10.0)
    # 18x 仓: 自身 < cap(20),保持 18,绝不被 target=10 拉低,也不被任何 max 抬升
    assert pos_ok.leverage == 18.0

