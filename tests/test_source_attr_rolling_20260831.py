"""source_attribution 滚动窗口 shadow 回归测试（2026-08-31 根因修复）。

根因：shadow 判定原先用全历史累计期望（_stats 总 gross/fee），来源一旦
shadow 几乎永无退出（退出需累计净期望回正，而 shadow 期间几乎无新样本），
实测 5/7 币 factor|scalp 长期 standdown、短线 406 次拦截。
修复：只看最近 SOURCE_ATTR_ROLLING_WINDOW 笔净收益；旧亏损随时间滚出窗口，
近期转正的来源自动复活；加载持久化状态时清空旧累计制 shadow 标志。
"""
import json

import backend.services.source_attribution as sa


def _fresh(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(sa, "_STATE_PATH", str(tmp_path / "attr.json"))
    monkeypatch.setattr(sa, "_record_attribution_row", lambda **k: None)
    inst = sa.SourceAttribution()
    inst._loaded = True  # 不加载真实 state 文件
    return inst


def test_rolling_window_revives_shadow_after_losses_roll_out(monkeypatch, tmp_path):
    inst = _fresh(monkeypatch, tmp_path)
    # 20 笔亏损 → 样本达标且滚动净期望 < 0 → shadow
    for i in range(20):
        inst.record_close(1000 + i, pnl=-1.0, fee=0.0, close_reason="test_loss",
                          tier="short", symbol="BTC", source="f", nature="s")
    assert inst.credit("f", "s", "BTC") == 0.0

    # 连续 30 笔盈利 → 亏损全部滚出窗口 → 自动复活
    for i in range(30):
        inst.record_close(2000 + i, pnl=1.0, fee=0.0, close_reason="test_win",
                          tier="short", symbol="BTC", source="f", nature="s")
    assert inst.credit("f", "s", "BTC") == 1.0

    # 滚动列表长度封顶
    with inst._lock:
        assert len(inst._stats["f|s|BTC"]["recent"]) == sa._ROLLING_WINDOW


def test_shadow_stays_while_recent_still_negative(monkeypatch, tmp_path):
    inst = _fresh(monkeypatch, tmp_path)
    for i in range(20):
        inst.record_close(1000 + i, pnl=-1.0, fee=0.0, close_reason="test_loss",
                          tier="short", symbol="BTC", source="f", nature="s")
    assert inst.credit("f", "s", "BTC") == 0.0
    # 只补 10 笔盈利：窗口仍是 20 亏 + 10 盈 → 期望仍负 → 保持 shadow
    for i in range(10):
        inst.record_close(2000 + i, pnl=1.0, fee=0.0, close_reason="test_win",
                          tier="short", symbol="BTC", source="f", nature="s")
    assert inst.credit("f", "s", "BTC") == 0.0


def test_old_persisted_cumulative_shadow_cleared_on_load(monkeypatch, tmp_path):
    p = tmp_path / "old.json"
    p.write_text(json.dumps({
        "tags": {}, "stats": {},
        "shadow": {"f|s|BTC": True},          # 旧累计制 shadow → 应清空
        "breaker": {},
        "breaker_shadow": {"short|x": True},  # 旧通道熔断 → 应清空
    }), encoding="utf-8")
    monkeypatch.setattr(sa, "_STATE_PATH", str(p))
    inst = sa.SourceAttribution()
    inst._ensure_loaded()
    assert inst._shadow == {}
    assert inst._breaker_shadow == {}


def test_prewarm_period_no_shadow_below_min_samples(monkeypatch, tmp_path):
    inst = _fresh(monkeypatch, tmp_path)
    for i in range(10):
        inst.record_close(1000 + i, pnl=-1.0, fee=0.0, close_reason="test_loss",
                          tier="short", symbol="BTC", source="f", nature="s")
    # 10 笔 < _MIN_SAMPLES(20) → 预热期不 shadow（敞口由探针 0.125x/日配额限制）
    assert inst.credit("f", "s", "BTC") == 1.0
