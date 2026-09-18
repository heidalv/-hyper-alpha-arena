# -*- coding: utf-8 -*-
"""轮96 回归：长线趋势仓不再被「收紧追踪止损」在最短持仓期内收割。

事故（reports/_轮96_长线趋势仓被收紧止损收割事故复盘_20260918.md）
------------------------------------------------------------------
2026-09-18 六小时内 7 笔仓位被全平，`close_reason` 清一色 `breakeven_tp`
（= `reason='sl'` 且 `pnl>0` 被 `normalize_close_reason` 改写，本质是**止损线成交**）：

| 车道 | 仓位 | 峰值 | 持仓 | 止损被谁拉到哪 |
|---|---|---|---|---|
| long | ETH/SOL/BNB/LINK | +3.0%~+6.0% | 9.4–13.6h | `midlong_position_manager` 的 tighten_trailing：`SL = 现价 − 2×短周期ATR`（≈1%） |
| mid | ASTER/XRP/SOL | +1.3%~+1.6% | 1.0–8.5h | `exit_policy` trailing：`SL = 峰值 − 0.5%`（`.env` 配置） |

三处结构性问题：
1. **契约缺守卫**：`trend_e1_engine` 文档声明 E1 长车道"唯一出场 = 规则失效/Chandelier"、
   "midlong 循环跳过"，但 `midlong_position_manager` 全文没有一处 `is_e1_position`。
2. **带宽过窄**：短周期 ATR × 2.0 ≈ 1% 的追踪带宽，用在设计持仓 3–7 天的车道上。
3. **保护被绕过**：硬线成交不看 `min_hold`（`unified_exit_state_machine:10`），
   于是"把止损挪到成本上方 1%"就成了绕过 72h 纪律的平仓通道。
   实证：08:27 同一笔 ETH 的**主动平仓**被 min_hold 拦下（日志可查），21:03 却被抬高后的
   止损线平掉。

本文件覆盖用户确认的四项修复（A/B/C/D）。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto.midlong_position_manager import (  # noqa: E402
    clamp_tighten_band,
    manage_position,
)

_MPM = ROOT / "backend" / "services" / "full_auto" / "midlong_position_manager.py"
_PTE = ROOT / "backend" / "services" / "paper_trading_engine.py"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


# ══════════════════════════════════════════════════════════════════════
# Fix A —— E1 长车道独占：midlong 管理器不得管理 E1 趋势仓
# ══════════════════════════════════════════════════════════════════════


def test_e1_position_is_skipped_by_manager():
    """行为断言：E1 仓位进 `manage_position` 必须立刻返回 skip，不碰 db/host。"""
    pos = {"id": 4712, "symbol": "BTC", "side": "long",
           "exit_state": {"entry_source": "trend_e1"}}
    out = manage_position(
        None, host=None, session=None, account_id=14, symbol="BTC",
        position=pos, market_summary={}, analyst_reports={}, trading_mode="paper",
    )
    assert out.get("action") == "manage_skip_e1", out
    assert out.get("hold_reason") == "e1_exclusive_lane_skip"


def test_non_e1_position_is_not_skipped_early():
    """反向断言：非 E1 仓位不得被这个守卫拦掉（否则 mid 车道管理整体失效）。"""
    from backend.services.trend_e1_engine import is_e1_position

    assert is_e1_position({"exit_state": {"entry_source": "trend_e1"}}) is True
    assert is_e1_position({"exit_state": {"entry_source": "other"}}) is False
    assert is_e1_position({"exit_state": {}}) is False


def test_guard_is_before_any_side_effect():
    """守卫必须放在函数前部：前面不得出现 append_event / _exec_tighten 等副作用。"""
    live = _strip_comments(_MPM.read_text(encoding="utf-8"))
    body = live[live.index("def manage_position("):]
    body = body[: body.index("def ", 10)] if "\ndef " in body else body
    idx_guard = body.index("is_e1_position")
    for side_effect in ("append_event", "_exec_tighten", "_exec_reduce", "_exec_pyramid",
                        "close_position", "resolve_thesis_hard_exit"):
        if side_effect in body:
            assert body.index(side_effect) > idx_guard, (
                f"{side_effect} 出现在 E1 守卫之前 —— 守卫必须最先生效"
            )


def test_e1_contract_is_documented_in_both_places():
    """守卫的存在理由要写在代码里（否则下次又会被当成冗余删掉）。"""
    src = _MPM.read_text(encoding="utf-8")
    assert "E1 独占" in src and "is_e1_position" in src
    assert "轮96" in src
    from backend.services import trend_e1_engine as e1
    doc = e1.__doc__ or ""
    assert "唯一出场" in doc or "Chandelier" in doc, "E1 侧契约文档仍在"


# ══════════════════════════════════════════════════════════════════════
# Fix B —— 车道最小收紧带宽
# ══════════════════════════════════════════════════════════════════════


def test_long_lane_band_is_widened_to_floor():
    sl, note = clamp_tighten_band(side="long", mark=100.0, new_sl=99.0, tier="long")
    assert sl == pytest.approx(97.0), "long 车道 1% 带宽必须被放宽到 3%"
    assert "Fix B" in note and "3.00%" in note


def test_wide_enough_band_is_untouched():
    """本来就有足够空间时不得改动（避免无意义地放松保护）。"""
    sl, note = clamp_tighten_band(side="long", mark=100.0, new_sl=96.0, tier="long")
    assert sl == pytest.approx(96.0) and note == ""


def test_mid_lane_floor_is_one_percent():
    sl, note = clamp_tighten_band(side="long", mark=100.0, new_sl=99.5, tier="mid")
    assert sl == pytest.approx(99.0)
    assert note


def test_short_lane_has_no_floor():
    """short 车道默认不设限（scalp 的 1% 带宽是它自己的设计）。"""
    sl, note = clamp_tighten_band(side="long", mark=100.0, new_sl=99.9, tier="short")
    assert sl == pytest.approx(99.9) and note == ""


def test_short_side_is_symmetric():
    sl, note = clamp_tighten_band(side="short", mark=100.0, new_sl=101.0, tier="long")
    assert sl == pytest.approx(103.0) and note
    sl2, note2 = clamp_tighten_band(side="short", mark=100.0, new_sl=104.0, tier="long")
    assert sl2 == pytest.approx(104.0) and note2 == ""


def test_helper_never_tightens():
    """反向不变式：该函数只能把止损推**远**，绝不能推近。"""
    for tier in ("long", "mid", "short"):
        for side in ("long", "short"):
            for proposed in (95.0, 99.0, 99.9, 100.1, 101.0, 105.0):
                out, _ = clamp_tighten_band(side=side, mark=100.0, new_sl=proposed, tier=tier)
                if side == "long":
                    assert out <= proposed + 1e-9, f"{tier}/{side} 把止损推近了: {proposed}→{out}"
                else:
                    assert out >= proposed - 1e-9, f"{tier}/{side} 把止损推近了: {proposed}→{out}"


def test_helper_is_env_overridable(monkeypatch):
    """下限必须可经配置覆盖，且覆盖真的生效（settings 优先、env 兜底）。"""
    from backend.config import settings as _st
    monkeypatch.setattr(_st, "MIDLONG_TIGHTEN_MIN_BAND_PCT_LONG", 0.05, raising=False)
    sl, note = clamp_tighten_band(side="long", mark=100.0, new_sl=97.0, tier="long")
    assert sl == pytest.approx(95.0) and note


def test_band_keys_are_declared_in_settings():
    """守卫：三个下限键必须在 settings 声明。

    `_cfg_float` 走 `getattr(settings, key, default)` —— 属性缺失时**直接返回默认值**，
    env 兜底根本轮不到。所以"只在 env 里设"是不生效的，必须在此钉住声明。
    """
    from backend.config import settings as _st
    for key in ("MIDLONG_TIGHTEN_MIN_BAND_PCT_LONG",
                "MIDLONG_TIGHTEN_MIN_BAND_PCT_MID",
                "MIDLONG_TIGHTEN_MIN_BAND_PCT_SHORT",
                "MIDLONG_MIN_LOCK_PROFIT_PCT_LONG",
                "MIDLONG_MIN_LOCK_PROFIT_PCT_MID",
                "MIDLONG_MIN_LOCK_PROFIT_PCT_SHORT"):
        assert hasattr(_st, key), f"{key} 未在 settings.py 声明 ⇒ env 覆盖静默失效"


def test_min_lock_defaults_match_lane_design():
    """锁利地板默认值：long 2.5%（用户确认的人工处置口径）、mid 0.5%、short 0。"""
    from backend.services.paper_trading_engine import paper_engine
    assert paper_engine._min_lock_profit_pct("long") == pytest.approx(0.025)
    assert paper_engine._min_lock_profit_pct("mid") == pytest.approx(0.005)
    assert paper_engine._min_lock_profit_pct("short") == pytest.approx(0.0)


def test_manager_calls_the_helper():
    live = _strip_comments(_MPM.read_text(encoding="utf-8"))
    assert "clamp_tighten_band(" in live
    # 旧的裸计算不得再直接落到 _exec_tighten
    body = live[live.index("if _review_action == \"tighten_trailing\":"):]
    body = body[: body.index("_exec_tighten(")]
    assert "clamp_tighten_band(" in body, "带宽下限必须在 _exec_tighten 之前生效"


# ══════════════════════════════════════════════════════════════════════
# Fix C —— min_hold 内拒付「追踪派生」止损
# ══════════════════════════════════════════════════════════════════════


class _Pos:
    """最小持仓替身（`_sl_min_hold_verdict` 只用 getattr 读属性）。"""

    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.symbol = kw.get("symbol", "ETH")
        self.side = kw.get("side", "long")
        self.entry_price = kw.get("entry_price", 100.0)
        self.sl_price = kw.get("sl_price", 103.0)
        self.mark_price = kw.get("mark_price", 102.0)
        self.margin = kw.get("margin", 50.0)
        self.unrealized_pnl = kw.get("unrealized_pnl", 2.0)
        self.peak_pnl_pct = kw.get("peak_pnl_pct", 0.03)
        self.timeframe_tier = kw.get("timeframe_tier", "long")
        self.trade_nature = kw.get("trade_nature", "trend_follow")
        self.status = "open"
        self.account_id = kw.get("account_id", 14)
        self.strategy_id = kw.get("strategy_id", "trend_e1:ETH")
        self.peak_unrealized_pnl = kw.get("peak_unrealized_pnl", 3.0)
        self.opened_at = kw.get("opened_at")
        self.exit_state_json = kw.get("exit_state_json", "{}")


def _es(**kw) -> str:
    base = {"structural_stop_price": kw.pop("structural", 95.0)}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


@pytest.fixture()
def engine():
    from backend.services.paper_trading_engine import paper_engine
    return paper_engine


def test_trailing_stop_inside_min_hold_is_deferred(engine):
    """核心断言：保护期内的追踪派生止损必须被拒付。"""
    pos = _Pos(
        entry_price=100.0, mark_price=110.0, sl_price=109.0,
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=10),
        exit_state_json=_es(sl_meta={"source": "trailing", "value": 109.0}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 110.0)
    assert defer is True, info
    assert info["structural_stop"] == pytest.approx(102.5), "回退位受锁利地板约束"
    assert info["min_hold_hours"] == pytest.approx(72.0)
    assert info["held_hours"] < 72


def test_shallow_profit_falls_back_to_structural(engine):
    """只浮盈 2% 时锁利地板(2.5%)在市价之上 ⇒ 退回结构位（趋势车道允许回吐到结构位）。

    这不是缺陷：min_hold 的意义就是"别在趋势没走完时被小回撤赶下车"，
    仓位此时本就该以结构位为风险边界继续持有。
    """
    pos = _Pos(
        entry_price=100.0, mark_price=102.0, sl_price=101.5,
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=10),
        exit_state_json=_es(structural=95.0, sl_meta={"source": "trailing"}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 102.0)
    assert defer is True and info["structural_stop"] == pytest.approx(95.0)


def test_restore_position_keeps_locked_profit(engine):
    """回退位必须 `max(结构位, 入场×(1+最小锁定利润))`：只回退到结构位会把锁利全还回去。

    E1 的 Chandelier 长期在入场价**之下**（趋势车道允许利润回吐到结构位），
    所以"放回结构位"与"保留已锁利润"必须靠这个地板同时满足。
    """
    pos = _Pos(
        entry_price=100.0, mark_price=110.0, sl_price=109.0,
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2),
        exit_state_json=_es(structural=95.0, sl_meta={"source": "trailing"}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 110.0)
    assert defer is True
    assert info["structural_stop"] == pytest.approx(102.5), "long 车道锁利地板 2.5%"


def test_structural_wins_when_it_is_higher(engine):
    """结构位高于锁利地板时用结构位（取更保护的那一个）。"""
    pos = _Pos(
        entry_price=100.0, mark_price=110.0, sl_price=109.0,
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2),
        exit_state_json=_es(structural=106.0, sl_meta={"source": "trailing"}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 110.0)
    assert defer is True and info["structural_stop"] == pytest.approx(106.0)


def test_lock_floor_on_wrong_side_falls_back_to_structural(engine):
    """锁利地板若跑到市价的错误一侧（会立即再触发），退回结构位。"""
    pos = _Pos(
        entry_price=100.0, mark_price=101.0, sl_price=100.9,
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2),
        exit_state_json=_es(structural=90.0, sl_meta={"source": "trailing"}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 101.0)
    # 102.5 > 101 ⇒ 地板在错误一侧 ⇒ 退回结构位 90
    assert defer is True and info["structural_stop"] == pytest.approx(90.0)


def test_after_min_hold_trailing_stop_fires(engine):
    pos = _Pos(
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=80),
        exit_state_json=_es(sl_meta={"source": "trailing"}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 102.0)
    assert defer is False and "已过保护期" in info["why"]


def test_structural_stop_is_never_deferred(engine):
    """结构位止损（Chandelier / 初始 SL）必须照常成交 —— 保护期不挡真实止损。"""
    pos = _Pos(
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        exit_state_json=_es(sl_meta={"source": "structural"}),
    )
    assert engine._sl_min_hold_verdict(pos, 102.0)[0] is False
    # 未标注来源同样按结构位对待（保持既有行为）
    pos2 = _Pos(opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1))
    assert engine._sl_min_hold_verdict(pos2, 102.0)[0] is False


def test_loss_zone_stop_is_never_deferred(engine):
    """止损落在亏损区 ⇒ 这是真实止损，任何情况都成交。"""
    pos = _Pos(
        entry_price=100.0, mark_price=98.0, sl_price=98.0,
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        exit_state_json=_es(sl_meta={"source": "trailing"}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 98.0)
    assert defer is False and "亏损区" in info["why"]


def test_emergency_loss_is_never_deferred(engine):
    """保护期内的紧急亏损阈值一旦触发，直接成交。"""
    pos = _Pos(
        margin=100.0, unrealized_pnl=-7.0,      # long 车道阈值 5%
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        exit_state_json=_es(sl_meta={"source": "trailing"}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 102.0)
    assert defer is False and "紧急亏损" in info["why"]


def test_missing_structural_stop_is_not_deferred(engine):
    """没有结构位可回退时必须成交 —— 否则下一 tick 再次触发会死循环。"""
    pos = _Pos(
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        exit_state_json=json.dumps({"sl_meta": {"source": "trailing"}}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 102.0)
    assert defer is False and "无结构位" in info["why"]


def test_tier_without_protection_is_not_deferred(engine):
    """research 车道 min_hold=0（无保护期）⇒ 不拒付。"""
    pos = _Pos(
        timeframe_tier="research", trade_nature="pair_research",
        opened_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5),
        exit_state_json=_es(sl_meta={"source": "trailing"}),
    )
    defer, info = engine._sl_min_hold_verdict(pos, 102.0)
    assert defer is False and "无保护期" in info["why"]


def test_verdict_fails_open_to_real_stop(engine):
    """任何异常都必须退回"照常止损"，绝不能因为状态读不出来而挡住保护。"""
    pos = _Pos(opened_at="不是时间",
               exit_state_json=_es(sl_meta={"source": "trailing"}))
    defer, _info = engine._sl_min_hold_verdict(pos, 102.0)
    assert defer is False


def test_sl_meta_stamp_and_read_roundtrip(engine):
    pos = _Pos(exit_state_json="{}")
    pos.sl_price = 103.0
    engine._stamp_sl_source(pos, "trailing", old_value=101.0)
    meta = engine.sl_meta_of(pos)
    assert meta.get("source") == "trailing"
    assert meta.get("value") == pytest.approx(103.0)
    assert meta.get("prev") == pytest.approx(101.0)
    assert meta.get("set_at")


def test_trailing_writers_are_tagged():
    """所有"追踪派生"止损写入点必须带 `sl_source="trailing"`，否则 Fix C 对它们无效。"""
    expected = {
        "backend/services/full_auto/midlong_position_manager.py": 1,
        "backend/services/position_exit_orchestrator.py": 2,
        "backend/services/full_auto/analyst_system_cycle.py": 1,
        "backend/services/full_auto/scalp_position_review.py": 1,
        "backend/services/full_auto_trading_service.py": 2,
    }
    for rel, n in expected.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        got = src.count('sl_source="trailing"')
        assert got == n, f"{rel} 的 trailing 标注数 {got} ≠ {n}"


def test_structural_writer_is_not_tagged_as_trailing():
    """E1 的 Chandelier / 紧急止损必须**不**带 trailing 标记（否则会被误拒付）。"""
    e1 = (ROOT / "backend/services/trend_e1_engine.py").read_text(encoding="utf-8")
    assert 'sl_source="trailing"' not in e1, "E1 Chandelier 是结构位，不得标成追踪派生"


def test_fast_path_consults_the_verdict_before_closing():
    live = _strip_comments(_PTE.read_text(encoding="utf-8"))
    body = live[live.index("def reprice_position("):]
    idx_verdict = body.index("_sl_min_hold_verdict")
    idx_close = body.index("self.close_position(")
    assert idx_verdict < idx_close, "min_hold 拒付判定必须在成交之前"
    assert "_record_sl_defer_event" in body, "拒付必须落库留痕（日志会被轮转）"


# ══════════════════════════════════════════════════════════════════════
# Fix D —— 中车道追踪参数放宽（配置项，改 .env）
# ══════════════════════════════════════════════════════════════════════


def test_mid_trailing_config_is_widened():
    """`.env`：中车道追踪激活 1.0→2.5、回调 0.5→1.2（治"涨 1.5% 回吐 0.5% 就走"）。"""
    env = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    assert "EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT=2.5" in env
    assert "EXIT_POLICY_MID_TRAILING_CALLBACK_PCT=1.2" in env
    # 生效值不得含编码损坏字符
    for line in env.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "EXIT_POLICY_MID_TRAILING" not in s:
            continue
        assert "?" not in s.split("#", 1)[0], f".env 生效值损坏: {s[:80]}"


def test_lane_policy_reads_env_so_change_takes_effect():
    """改 .env 必须真的生效：`ExitPolicy.for_lane("mid")` 要读到这两个键。"""
    import os
    from unittest.mock import patch

    from backend.services.exit.exit_policy import ExitPolicy

    with patch.dict(os.environ, {
        "EXIT_POLICY_MID_TRAILING_ACTIVATION_PCT": "2.5",
        "EXIT_POLICY_MID_TRAILING_CALLBACK_PCT": "1.2",
    }):
        pol = ExitPolicy.for_lane("mid")
        assert float(pol.trailing_activation_pct) == pytest.approx(2.5)
        assert float(pol.trailing_callback_pct) == pytest.approx(1.2)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
