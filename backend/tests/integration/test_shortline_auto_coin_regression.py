"""
阶段0 Task3 回归测试：auto-coin 解耦是 tier 作用域的（短线可见 / 长线不可见）。

背景（升级计划 §6.3 + 边界审计）：
  共享函数 `_resolve_session_trade_symbols`（full_auto_trading_service.py:3939）
  是短线 / 中长线 / 编排器 共用的「会话交易 universe」单一来源。它必须仍然返回
  含 auto-coin 的完整合并集（symbols + auto_coin_symbols + 持仓 + 策略），因为
  短线（scalp_loop.py:49）直接消费它来扫币——一旦有人把它「全局解耦」成只返回
  固定币，短线就再也看不到 AI 选的币，被无声饿死。

  长线的 auto-coin 排除必须发生在「消费点」：mlto_cycle.py 通过
  `get_fixed_symbols_for_session`（auto_coin_selector.py:2637）拿正向白名单
  `fixed - auto_set`，对不在白名单的 symbol `continue` 跳过。

  本测试固化这一边界：
    - 短线（_resolve_session_trade_symbols）能看见 auto-coin；
    - 长线（get_fixed_symbols_for_session）看不见 auto-coin。
  防止后续升级误把共享函数全局改造，饿死短线。

运行：
  python -m pytest backend/tests/integration/test_shortline_auto_coin_regression.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import MagicMock, patch

# 被测对象
from backend.services.full_auto_trading_service import FullAutoTradingService
from backend.services import auto_coin_selector


# ════════════════════════════════════════════════════════════════════
# 短线 auto-coin 可见性 / 长线 auto-coin 排除 分离回归（阶段0 Task3）
# ════════════════════════════════════════════════════════════════════
class TestShortlineAutoCoinRegression:
    """验证 auto-coin 解耦是 tier 作用域，不是全局。"""

    SESSION_ID = "sess_test_shortline_auto_coin"
    FIXED_SYMBOLS = ["BTC", "ETH"]          # 会话固定配置
    AUTO_COIN_SYMBOLS = ["DOGE", "PEPE"]    # AI 选币（动态轮换）

    # ────────────────────────────────────────────────────────────────
    # 辅助：构造 mock session（带 symbols + auto_coin_symbols）
    # ────────────────────────────────────────────────────────────────
    def _build_mock_session(self):
        session = MagicMock()
        session.session_id = self.SESSION_ID
        session.symbols = list(self.FIXED_SYMBOLS)
        session.auto_coin_symbols = list(self.AUTO_COIN_SYMBOLS)
        return session

    # ────────────────────────────────────────────────────────────────
    # 辅助：构造 service 实例（绕过单例 __init__ 的重型初始化）
    # _resolve_session_trade_symbols 在 db=None 时不读任何 self 状态，
    # 只读 session.symbols / session.auto_coin_symbols，故 object.__new__ 足够。
    # ────────────────────────────────────────────────────────────────
    def _build_service(self):
        return object.__new__(FullAutoTradingService)

    # ════════════════════════════════════════════════════════════════
    # 短线侧：_resolve_session_trade_symbols 必须包含 auto-coin
    # ════════════════════════════════════════════════════════════════
    def test_resolve_symbols_includes_auto_coin(self):
        """核心回归：_resolve_session_trade_symbols 仍返回含 auto-coin 的完整宇宙。

        短线（scalp_loop）直接消费此结果扫币——若 auto-coin 被全局移除，
        短线再也扫不到 AI 选的币（饿死）。这是阶段0升级最需保护的边界。
        """
        svc = self._build_service()
        session = self._build_mock_session()

        # db=None → 跳过持仓 / 策略合并分支，聚焦核心 symbols + auto_coin 合并
        result = svc._resolve_session_trade_symbols(session, db=None)

        result_set = set(result)
        # 固定币必须在
        for sym in self.FIXED_SYMBOLS:
            assert sym in result_set, f"固定币 {sym} 必须在 universe 内"
        # 关键断言：auto-coin 必须也在（短线依赖）
        for sym in self.AUTO_COIN_SYMBOLS:
            assert sym in result_set, (
                f"auto-coin {sym} 必须在 _resolve_session_trade_symbols 结果内"
                f"（短线依赖完整宇宙）。当前结果={sorted(result_set)}"
            )

    def test_resolve_symbols_returns_uppercase_deduped(self):
        """_resolve_session_trade_symbols 应做大写归一 + 去重。"""
        svc = self._build_service()
        session = self._build_mock_session()
        # 故意制造大小写 + 重复
        session.symbols = ["btc", "ETH", "btc"]
        session.auto_coin_symbols = ["doge", "PEPE", "eth"]

        result = svc._resolve_session_trade_symbols(session, db=None)

        # 全大写
        assert all(s == s.upper() for s in result), "结果应全大写"
        # 去重（ETH 在两侧都出现只算一次）
        assert len(result) == len(set(result)), "结果应去重"
        assert set(result) == {"BTC", "ETH", "DOGE", "PEPE"}

    # ════════════════════════════════════════════════════════════════
    # 长线侧：get_fixed_symbols_for_session 必须排除 auto-coin
    # ════════════════════════════════════════════════════════════════
    def test_get_fixed_symbols_excludes_auto_coin(self):
        """核心回归：get_fixed_symbols_for_session 返回 fixed - auto_set。

        长线（mlto_cycle.py）拿此白名单，对不在其中的 symbol continue 跳过——
        即 auto-coin 永远进不了长线开仓。
        """
        db = MagicMock()
        # 模拟 raw SQL SELECT symbols, auto_coin_symbols FROM ... 的返回行
        mock_row = (list(self.FIXED_SYMBOLS), list(self.AUTO_COIN_SYMBOLS))
        db.execute.return_value = MagicMock()
        db.execute.return_value.first.return_value = mock_row

        result = auto_coin_selector.get_fixed_symbols_for_session(
            self.SESSION_ID, db=db
        )

        # 固定币必须在白名单
        for sym in self.FIXED_SYMBOLS:
            assert sym in result, f"固定币 {sym} 应在长线白名单内"
        # 关键断言：auto-coin 必须不在（长线依赖排除）
        for sym in self.AUTO_COIN_SYMBOLS:
            assert sym not in result, (
                f"auto-coin {sym} 必须不在长线白名单内（长线不能交易 auto-coin）。"
                f"当前白名单={sorted(result)}"
            )

    def test_get_fixed_symbols_raw_sql_query_shape(self):
        """验证 get_fixed_symbols_for_session 使用 raw SQL 现查 DB
        （不依赖可能 stale 的 ORM 对象），与 auto_coin_selector.py:2663 一致。
        这是 [2026-07-21 修复] 的关键设计：每次毫秒级现查最新行。
        """
        db = MagicMock()
        mock_row = (["BTC"], ["DOGE"])
        db.execute.return_value = MagicMock()
        db.execute.return_value.first.return_value = mock_row

        auto_coin_selector.get_fixed_symbols_for_session(self.SESSION_ID, db=db)

        # 确认走了 db.execute(sql_text, params) 路径
        assert db.execute.called, "应通过 db.execute 现查 DB"
        call_args = db.execute.call_args
        # 第一个位置参数是 SQL text 对象（不是 ORM query）
        sql_arg = call_args[0][0]
        # SQLAlchemy text() 对象渲染出的字符串包含表名
        sql_str = str(sql_arg)
        assert "full_auto_sessions" in sql_str, "SQL 应查询 full_auto_sessions 表"
        # 第二个位置参数是绑定参数 dict（db.execute(sql, {"sid": ...})）
        # 优先取位置参数，回退到 kwargs，兼容两种调用写法
        params = call_args[0][1] if len(call_args[0]) > 1 else (call_args[1] or {})
        assert params.get("sid") == self.SESSION_ID, "应按 session_id 参数化查询"

    def test_get_fixed_symbols_no_row_returns_empty(self):
        """session 不存在时返回空集（容错）。"""
        db = MagicMock()
        db.execute.return_value = MagicMock()
        db.execute.return_value.first.return_value = None

        result = auto_coin_selector.get_fixed_symbols_for_session(
            "sess_nonexistent", db=db
        )
        assert result == set()

    # ════════════════════════════════════════════════════════════════
    # 分离验证：同一 session，短长线对 auto-coin 的可见性相反
    # ════════════════════════════════════════════════════════════════
    def test_separation_short_sees_auto_coin_long_does_not(self):
        """端到端分离断言：同一份 session 配置下，
        短线 universe 含 auto-coin，长线白名单不含——即 tier 作用域正确。
        """
        # 短线侧
        svc = self._build_service()
        session = self._build_mock_session()
        short_universe = set(svc._resolve_session_trade_symbols(session, db=None))

        # 长线侧（同一 session 数据）
        db = MagicMock()
        mock_row = (list(self.FIXED_SYMBOLS), list(self.AUTO_COIN_SYMBOLS))
        db.execute.return_value = MagicMock()
        db.execute.return_value.first.return_value = mock_row
        long_whitelist = auto_coin_selector.get_fixed_symbols_for_session(
            self.SESSION_ID, db=db
        )

        # 分离断言
        auto_set = set(self.AUTO_COIN_SYMBOLS)
        assert auto_set.issubset(short_universe), (
            "短线必须能看见全部 auto-coin"
        )
        assert auto_set.isdisjoint(long_whitelist), (
            "长线白名单必须与 auto-coin 完全不相交"
        )
        # 两者都包含固定币
        fixed_set = set(self.FIXED_SYMBOLS)
        assert fixed_set.issubset(short_universe)
        assert fixed_set.issubset(long_whitelist)
        # 长线白名单严格小于短线 universe（差集正是 auto-coin）
        assert long_whitelist < short_universe
        assert short_universe - long_whitelist == auto_set, (
            "短线比长线多出的部分应恰好等于 auto-coin"
        )
