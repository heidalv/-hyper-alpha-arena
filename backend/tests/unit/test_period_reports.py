# -*- coding: utf-8 -*-
"""轮63 双车道报告体系测试 —— 车道词表 / 车道解析 / 日报结构 / 亏损归因 / 指挥指令。

本轮重构的核心断言：**入参用什么名字都不影响结果的车道归属**，
且每段报告自带周期身份（lane_identity），前端无需猜测。
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.config import lane_semantics as lane_sem
from backend.services.loss_attribution import build_loss_attribution, lane_of_trade


# ══════════════════════════════════════════════════════════════════════
# 车道词表（单一真源）
# ══════════════════════════════════════════════════════════════════════

def test_report_lanes_are_two():
    """报告只有两条车道：日内 + 长线趋势（scalp 不再是独立车道）。"""
    assert lane_sem.REPORT_LANES == ("intraday", "trend")
    assert lane_sem.LANE_INTRADAY not in (lane_sem.LANE_RESEARCH,)
    # 研究车道存在但不进交易报告
    assert lane_sem.LANE_RESEARCH in lane_sem.ALL_LANES
    assert lane_sem.LANE_RESEARCH not in lane_sem.REPORT_LANES
    assert lane_sem.is_report_lane("research") is False


def test_every_nature_and_tier_resolves():
    """全库实际出现过的 (nature, tier) 组合都落到预期车道 —— 用实测分布做基线。"""
    # 来自 paper_positions 全库实测
    cases = {
        ("scalp", "short"): "intraday",          # 3041 笔
        ("intraday", "short"): "intraday",       # 3 笔 —— 旧实现把它兜底成 scalp
        ("swing", "mid"): "intraday",            # 288 笔（中线槽位实测中位 4.0h）
        ("trend_follow", "long"): "trend",       # 74 笔
        ("position", "long"): "trend",           # 7 笔
        ("pair_research", "research"): "research",
        ("research", "research"): "research",
    }
    for (nature, tier), expected in cases.items():
        assert lane_sem.resolve_lane(nature, tier) == expected, (nature, tier)


def test_intraday_is_not_silently_bucketed_as_scalp():
    """回归旧 bug：nature=intraday 曾掉进 `_horizon_of` 的 scal 兜底洞。"""
    assert lane_sem.resolve_lane("intraday", "short") == lane_sem.LANE_INTRADAY
    assert lane_sem.resolve_lane("intraday", None) == lane_sem.LANE_INTRADAY
    assert lane_sem.resolve_lane(None, "short") == lane_sem.LANE_INTRADAY


def test_nature_wins_over_tier_and_default_is_intraday():
    assert lane_sem.resolve_lane("trend_follow", "mid") == "trend"   # nature 优先
    assert lane_sem.resolve_lane(None, None) == lane_sem.DEFAULT_LANE
    assert lane_sem.DEFAULT_LANE == "intraday"
    # 未知标签不抛异常（报告路径不能因一条脏数据整段消失）
    assert lane_sem.resolve_lane("???", "!!!") == "intraday"


def test_legacy_horizon_read_compat():
    """旧枚举与中文展示名都能翻译过来 —— 旧前端 / 深链参数不会变空白。"""
    for legacy in ("scalp", "midlong", "short", "mid", "intraday"):
        assert lane_sem.lane_for_label(legacy) == "intraday", legacy
    for legacy in ("long", "trend", "longterm"):
        assert lane_sem.lane_for_label(legacy) == "trend", legacy
    assert lane_sem.lane_for_label("长线趋势") == "trend"
    assert lane_sem.lane_for_label("日内波段") == "intraday"
    assert lane_sem.lane_for_label("中线") == "intraday"
    assert lane_sem.lane_for_label("  MIDLONG ") == "intraday"
    assert lane_sem.lane_for_label(None) is None
    assert lane_sem.lane_for_label("") is None


def test_lane_identity_is_self_describing():
    """每条车道自带周期身份 —— 这是「分不清哪个是哪个」的结构性修复。"""
    ident = lane_sem.lane_identity("midlong")
    assert ident["lane"] == "intraday"
    assert ident["tier"] == "mid"
    assert ident["nature"] == "swing"
    assert ident["report_timeframe"] == "1h"
    assert ident["confirm_timeframes"] == ["15m", "4h"]
    assert ident["label_full"]

    trend = lane_sem.lane_identity("long")
    assert trend["lane"] == "trend"
    assert trend["report_timeframe"] == "4h"
    assert trend["tier"] == "long"
    # 两条车道的周期身份必须可区分
    assert ident["report_timeframe"] != trend["report_timeframe"]
    assert ident["tier"] != trend["tier"]


def test_hold_bracket_labels_differ():
    assert lane_sem.hold_bracket_label("intraday") == "0.5h–24h"
    assert lane_sem.hold_bracket_label("trend") == "24h–168h"


def test_mismatch_detection():
    """标签矛盾的仓位要被识别出来，而不是静默塞进某条车道。"""
    assert lane_sem.lane_mismatch("swing", "mid") is None
    assert lane_sem.lane_mismatch("trend_follow", "long") is None
    assert lane_sem.lane_mismatch("swing", "long") is not None
    assert lane_sem.lane_mismatch("scalp", "long") is not None
    assert lane_sem.lane_mismatch(None, "long") is None   # 信息不足不算矛盾


def test_lane_to_engine_labels():
    """写回方向：车道 → 引擎标签，保证开仓/存档用规范值。"""
    assert lane_sem.lane_to_tier("intraday") == "mid"
    assert lane_sem.lane_to_nature("intraday") == "swing"
    assert lane_sem.lane_to_tier("trend") == "long"
    assert lane_sem.lane_to_nature("trend") == "trend_follow"


# ══════════════════════════════════════════════════════════════════════
# 亏损归因
# ══════════════════════════════════════════════════════════════════════

def _mk_pos(symbol="BTC", pnl=-10.0, reason="chandelier", nature="trend_follow", tf="long"):
    """D3 口径：PaperPosition 风格 mock，pnl 经 entry/close 价差复原。"""
    p = MagicMock()
    p.symbol = symbol
    p.side = "long"
    p.partial_realized_pnl = 0.0
    p.close_reason = reason
    p.entry_price = 100.0
    p.size = 1.0
    p.close_price = 100.0 + pnl  # long: realized = (close-entry)*size
    p.trade_nature = nature
    p.timeframe_tier = tf
    return p


def _mk_db(rows):
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = rows
    db.query.return_value = q
    return db


def test_lane_of_trade_matches_single_source():
    """旧函数名保留，但结果必须与 lane_semantics 完全一致（不允许第二份逻辑）。"""
    assert lane_of_trade("trend_follow", None) == "trend"
    assert lane_of_trade("swing", None) == "intraday"
    assert lane_of_trade("intraday", "short") == "intraday"
    assert lane_of_trade("scalp", "short") == "intraday"
    assert lane_of_trade(None, "long") == "trend"
    assert lane_of_trade("pair_research", "research") == "research"


def test_loss_attribution_triggers_on_loss():
    db = _mk_db([_mk_pos("BTC", -20.0, "chandelier"),
                 _mk_pos("ETH", -5.0, "structure_break"),
                 _mk_pos("BTC", -8.0, "chandelier")])
    out = build_loss_attribution(db, 1, "trend", days=1)
    assert out["active"] is True
    assert out["lane"] == "trend"
    assert out["total_pnl"] == -33.0
    assert len(out["by_symbol"]) == 2  # BTC(-28) ETH(-5)
    assert out["by_symbol"][0]["key"] == "BTC"  # 亏损最重在前
    # 统一分桶结构：每项都有 key/pnl/n，前端不必猜哪一项缺 n
    for bucket in ("by_symbol", "by_exit_reason", "by_trade_nature"):
        for item in out[bucket]:
            assert set(item) == {"key", "pnl", "n"}, (bucket, item)


def test_loss_attribution_scopes_to_lane_only():
    """归因必须只统计本车道 —— 跨车道混算正是「分不清」的原始病灶。"""
    rows = [_mk_pos("BTC", -30.0, nature="trend_follow", tf="long"),
            _mk_pos("ETH", -50.0, nature="swing", tf="mid")]
    out = build_loss_attribution(_mk_db(rows), 1, "trend", days=1)
    assert out["total_pnl"] == -30.0          # 不含 swing 的 -50
    assert [x["key"] for x in out["by_symbol"]] == ["BTC"]


def test_loss_attribution_note_has_no_internal_jargon():
    """文案不得泄漏内部枚举名（旧版会输出「midlong 近 1 天盈利…」）。"""
    db = _mk_db([_mk_pos("BTC", 20.0, nature="swing", tf="mid")])
    out = build_loss_attribution(db, 1, "intraday", days=1)
    assert out["active"] is False
    assert "midlong" not in out["note"]
    assert "scalp" not in out["note"]
    assert out["lane_label"] == "日内"
    assert "盈利" in out["note"]


def test_loss_attribution_inactive_no_samples():
    out = build_loss_attribution(_mk_db([]), 1, "intraday", days=1)
    assert out["active"] is False and "无平仓样本" in out["note"]
    assert out["lane"] == "intraday"
    # 旧调用方传 scalp 也能工作（兼容别名）
    out2 = build_loss_attribution(_mk_db([]), 1, "scalp", days=1)
    assert out2["lane"] == "intraday"


# ══════════════════════════════════════════════════════════════════════
# 日报结构
# ══════════════════════════════════════════════════════════════════════

def _mock_report_db():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = []
    q.first.return_value = None
    db.query.return_value = q
    return db


def test_daily_report_builds_two_lane_sections():
    """日报产出两条车道，每段自带 lane_identity；长线趋势段含 L1 面板。"""
    from backend.services.period_daily_report import build_daily_report
    db = _mock_report_db()
    with patch("backend.services.period_daily_report._l1_panel",
               return_value={"BTC": {"state": "up"}}), \
         patch("backend.services.period_daily_report._open_positions", return_value=[]), \
         patch("backend.services.loss_attribution.build_loss_attribution",
               return_value={"active": False, "note": "无样本"}):
        rep = build_daily_report(db, 1)
    assert set(rep["sections"].keys()) == {"intraday", "trend"}
    assert rep["lanes"] == ["intraday", "trend"]
    # scalp 不再是段
    assert "scalp" not in rep["sections"]
    assert "midlong" not in rep["sections"]

    assert rep["sections"]["trend"]["l1_panel"]["BTC"]["state"] == "up"
    assert "loss_attribution" in rep["sections"]["intraday"]
    # 每段自带周期身份
    for lane in ("intraday", "trend"):
        ident = rep["sections"][lane]["lane_identity"]
        assert ident["lane"] == lane
        assert ident["report_timeframe"]
        assert ident["expected_hold_hours"] > 0
    assert rep["sections"]["intraday"]["lane_identity"]["report_timeframe"] == "1h"
    assert rep["sections"]["trend"]["lane_identity"]["report_timeframe"] == "4h"


def test_daily_report_intraday_keeps_exit_stats():
    """D1 超时退出监控随 scalp→intraday 迁移，不能随短线车道停用而消失。"""
    from backend.services.period_daily_report import build_daily_report
    db = _mock_report_db()
    with patch("backend.services.period_daily_report._l1_panel", return_value={}), \
         patch("backend.services.period_daily_report._open_positions", return_value=[]), \
         patch("backend.services.loss_attribution.build_loss_attribution",
               return_value={"active": False, "note": "无样本"}):
        rep = build_daily_report(db, 1)
    assert "exit_stats" in rep["sections"]["intraday"]
    assert "exit_stats" not in rep["sections"]["trend"]


def test_daily_report_skips_l1_panel_for_historical_dates():
    """回填历史日期时不算 L1 面板（它取实时 K线，对历史日期既慢又无意义）。"""
    from backend.services.period_daily_report import build_daily_report
    db = _mock_report_db()
    with patch("backend.services.period_daily_report._l1_panel") as panel, \
         patch("backend.services.period_daily_report._open_positions", return_value=[]), \
         patch("backend.services.loss_attribution.build_loss_attribution",
               return_value={"active": False, "note": "无样本"}):
        rep = build_daily_report(db, 1, date="2026-01-01")
    panel.assert_not_called()
    assert "l1_panel" not in rep["sections"]["trend"]


def test_every_section_has_data_quality_block():
    """数据质量块显式暴露标签矛盾 —— 静默归并是「分不清」的成因。"""
    from backend.services.period_daily_report import build_daily_report
    db = _mock_report_db()
    with patch("backend.services.period_daily_report._l1_panel", return_value={}), \
         patch("backend.services.period_daily_report._open_positions", return_value=[]), \
         patch("backend.services.loss_attribution.build_loss_attribution",
               return_value={"active": False, "note": "无样本"}):
        rep = build_daily_report(db, 1)
    for lane in ("intraday", "trend"):
        dq = rep["sections"][lane]["data_quality"]
        assert dq["mismatched_labels"] == 0
        assert dq["untagged_trades"] == 0


# ══════════════════════════════════════════════════════════════════════
# 指挥指令
# ══════════════════════════════════════════════════════════════════════

def test_directives_iterate_lanes_and_carry_lane():
    """指令遍历车道（而非硬编码旧枚举），且每条带 lane 供按车道分发。"""
    from backend.services import report_directives as rd
    report = {
        "report_date": "2026-09-18",
        "lanes": ["intraday", "trend"],
        "sections": {
            "intraday": {"symbol_daily": [{"symbol": "BTC", "pnl": -12.0, "n": 3}]},
            "trend": {"symbol_daily": [{"symbol": "ETH", "pnl": -7.0, "n": 2}]},
        },
    }
    with patch("backend.services.symbol_penalty.update_daily",
               return_value={"watchlisted": True, "penalty": 0.5}):
        out = rd.analyze_directives(report)
    assert {d["lane"] for d in out} == {"intraday", "trend"}
    assert {d["symbol"] for d in out} == {"BTC", "ETH"}
    # horizon 保留为兼容字段且值已规范化
    for d in out:
        assert d["horizon"] == d["lane"]
        assert d["lane"] in ("intraday", "trend")


# ══════════════════════════════════════════════════════════════════════
# API 层
# ══════════════════════════════════════════════════════════════════════

def test_api_resolves_lane_and_legacy_horizon():
    """API 的 lane/horizon 双参数解析：旧值不被丢弃。"""
    from backend.api.period_reports_routes import _resolve_lane_or_none
    assert _resolve_lane_or_none("intraday") == "intraday"
    assert _resolve_lane_or_none("trend") == "trend"
    assert _resolve_lane_or_none("midlong") == "intraday"   # 旧前端深链
    assert _resolve_lane_or_none("long") == "trend"         # 旧前端深链
    assert _resolve_lane_or_none("scalp") == "intraday"
    assert _resolve_lane_or_none("长线趋势") == "trend"
    assert _resolve_lane_or_none(None) is None              # 未传 = 不过滤
    assert _resolve_lane_or_none("") is None
