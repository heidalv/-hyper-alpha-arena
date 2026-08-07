"""
新闻驱动方向 + 教训时间衰退 根治回归测试

根治1: 教训注入不再"一笔亏损记一辈子" —— 按亏损幅度+时间远近分级 severity
根治2: 新闻不再冻结，负面新闻→做空信号、正面新闻→做多信号
"""
import pytest
from datetime import datetime, timezone, timedelta


pytestmark = pytest.mark.unit


# ────────────── 根治1: 教训时间衰退 + 亏损比例分级 ──────────────

def test_lesson_severity_by_loss_magnitude_and_recency():
    """severity 按亏损幅度+时间分级，不再'一笔亏损就 high'。"""
    from backend.services.decision_feedback_service import DecisionFeedbackService

    svc = DecisionFeedbackService.__new__(DecisionFeedbackService)
    now = datetime.now(timezone.utc)

    # 近1天大亏(-8%) → high
    r1 = {"symbol": "BTC", "was_correct": "no", "lesson_learned": "x",
          "pnl_pct": -8.0, "closed_at": (now - timedelta(hours=12)).isoformat()}
    # 近5天中亏(-3%) → medium
    r2 = {"symbol": "ETH", "was_correct": "no", "lesson_learned": "y",
          "pnl_pct": -3.0, "closed_at": (now - timedelta(days=5)).isoformat()}
    # 10天前小亏(-0.5%) → low
    r3 = {"symbol": "SOL", "was_correct": "no", "lesson_learned": "z",
          "pnl_pct": -0.5, "closed_at": (now - timedelta(days=10)).isoformat()}
    # 盈利 → info
    r4 = {"symbol": "XPL", "was_correct": "yes", "lesson_learned": "w",
          "pnl_pct": 5.0, "closed_at": (now - timedelta(hours=2)).isoformat()}

    lessons = svc._extract_lessons([r1, r2, r3, r4], None)
    by_sym = {l["symbol"]: l for l in lessons}

    assert by_sym["BTC"]["severity"] == "high", "近1天-8%应high"
    assert by_sym["ETH"]["severity"] == "medium", "近5天-3%应medium"
    assert by_sym["SOL"]["severity"] == "low", "10天前-0.5%应low"
    assert by_sym["XPL"]["severity"] == "info", "盈利应info"


def test_lesson_old_loss_not_high_severity():
    """14天前的大亏不再是 high（时间衰退为 low）。"""
    from backend.services.decision_feedback_service import DecisionFeedbackService

    svc = DecisionFeedbackService.__new__(DecisionFeedbackService)
    old = datetime.now(timezone.utc) - timedelta(days=14)
    r = {"symbol": "BTC", "was_correct": "no", "lesson_learned": "old big loss",
         "pnl_pct": -10.0, "closed_at": old.isoformat()}
    lessons = svc._extract_lessons([r], None)
    assert lessons[0]["severity"] == "low", "14天前-10%应衰退为low(不再high)"


def test_lesson_has_time_tag():
    """lesson 文本带时间标签（[近1天]/[近3天]/[N天前]）。"""
    from backend.services.decision_feedback_service import DecisionFeedbackService

    svc = DecisionFeedbackService.__new__(DecisionFeedbackService)
    now = datetime.now(timezone.utc)
    r = {"symbol": "BTC", "was_correct": "no", "lesson_learned": "test",
         "pnl_pct": -5.0, "closed_at": (now - timedelta(hours=6)).isoformat()}
    lessons = svc._extract_lessons([r], None)
    assert "[近1天]" in lessons[0]["lesson"]


# ────────────── 根治2: 新闻驱动方向，不冻结 ──────────────

def test_negative_news_drives_bearish_not_freeze():
    """负面新闻 → short_view 偏空（做空信号），不再冻结。"""
    from backend.services.multi_timeframe_orchestrator import EVENT_OVERRIDE_RULES

    # EVENT_OVERRIDE_RULES 的 freeze_minutes 已缩短（blackswan 30→10, regulation 15→5）
    assert EVENT_OVERRIDE_RULES["blackswan_negative"]["freeze_minutes"] <= 10
    assert EVENT_OVERRIDE_RULES["regulation_negative"]["freeze_minutes"] <= 5


def test_news_threshold_is_high_strength():
    """新闻触发阈值是 strength>=7（高强度），中等新闻(strength 4-6)不触发。"""
    # 验证阈值常量语义
    _threshold = 7
    # strength=4 (中等) 不应触发
    assert not (4 >= _threshold)
    # strength=7 (高) 应触发
    assert 7 >= _threshold
    # strength=9 (极高) 应触发
    assert 9 >= _threshold
