"""parity_score 冻结 TTL 与判据修复回归测试（2026-08-31 根因修复）。

根因链：scalp 8/29 被 parity 冻结后短线圈 0 开仓——冻结源于
① sharpe 7天窗符号翻转打满 dev 截断上限单指标击穿 core_score；
② 冻结后无新成交 → 样本老化 → 只有每周日扫描才可能解冻（停摆近 2 周）。
修复：sharpe 移出 CORE_FREEZE_METRICS；冻结带 TTL（FREEZE_MAX_DAYS），
unified_gate 热路径 parity_prune_expired() 强制过期解除。
"""
import json
import time
from datetime import datetime, timedelta, timezone

import backend.services.backtest_engine.parity_score as ps


def test_sharpe_not_in_core_freeze_metrics():
    assert "sharpe" not in ps.CORE_FREEZE_METRICS
    assert {"win_rate", "profit_factor", "max_drawdown"} <= ps.CORE_FREEZE_METRICS
    # score/告警口径权重仍保留 sharpe（仪表盘如实上报）
    assert "sharpe" in ps.WEIGHTS


def test_record_freeze_breach_sets_frozen_at_on_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "FREEZE_STATE_PATH", str(tmp_path / "fs.json"))
    assert ps._record_freeze_breach("scalp", breached=True) == 1
    st = ps._load_freeze_state()["scalp"]
    assert st["count"] == 1
    assert st["frozen_at"] == ""  # 未达连续次数门槛不落 frozen_at
    assert ps._record_freeze_breach("scalp", breached=True) == 2
    st = ps._load_freeze_state()["scalp"]
    assert st["frozen_at"]  # 连续2次达标 → 记录冻结时间
    # 未触发 → 计数与时间戳清零
    ps._record_freeze_breach("scalp", breached=False)
    st = ps._load_freeze_state()["scalp"]
    assert st["count"] == 0 and st["frozen_at"] == ""


def test_legacy_int_state_migrates_to_dict(monkeypatch, tmp_path):
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"scalp": 2}), encoding="utf-8")
    monkeypatch.setattr(ps, "FREEZE_STATE_PATH", str(p))
    st = ps._load_freeze_state()
    assert st["scalp"]["count"] == 2
    assert st["scalp"]["frozen_at"]  # 旧格式视为当前已冻结 → TTL 接管


class _GovStub:
    def __init__(self):
        self.intents = []

    def submit_intent(self, key, value, **kw):
        self.intents.append((key, list(value), kw.get("reason", "")))


def test_prune_expired_unfreezes_and_submits_intent(monkeypatch, tmp_path):
    p = tmp_path / "fs.json"
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    p.write_text(json.dumps({
        "scalp": {"count": 2, "frozen_at": old},
        "swing": {"count": 2, "frozen_at": fresh},
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "FREEZE_STATE_PATH", str(p))
    monkeypatch.setattr(ps, "FREEZE_MAX_DAYS", 4.0)
    gov = _GovStub()
    import backend.services.runtime_governor as rg
    monkeypatch.setattr(rg, "runtime_governor", gov)
    # 清模块级缓存避免上一测试残留
    ps._freezee_cache.update({"ts": 0.0, "expired": []})

    out = ps.parity_prune_expired(["scalp", "swing"])
    assert out == ["swing"]  # scalp 过期移除，swing 保留
    assert gov.intents and gov.intents[0][0] == "disabled_natures"
    assert gov.intents[0][1] == ["swing"]
    # 状态文件已清零 scalp
    st = ps._load_freeze_state()["scalp"]
    assert st["count"] == 0 and st["frozen_at"] == ""


def test_prune_cache_within_60s(monkeypatch, tmp_path):
    p = tmp_path / "fs.json"
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    p.write_text(json.dumps({"scalp": {"count": 2, "frozen_at": old}}), encoding="utf-8")
    monkeypatch.setattr(ps, "FREEZE_STATE_PATH", str(p))
    monkeypatch.setattr(ps, "FREEZE_MAX_DAYS", 4.0)
    gov = _GovStub()
    import backend.services.runtime_governor as rg
    monkeypatch.setattr(rg, "runtime_governor", gov)
    ps._freezee_cache.update({"ts": time.time(), "expired": ["scalp"]})

    # 缓存命中：不读状态文件、不再提交意图，直接过滤
    out = ps.parity_prune_expired(["scalp", "swing"])
    assert out == ["swing"]
    assert gov.intents == []


def test_prune_keeps_when_no_frozen_at(monkeypatch, tmp_path):
    p = tmp_path / "fs.json"
    p.write_text(json.dumps({"scalp": {"count": 1, "frozen_at": ""}}), encoding="utf-8")
    monkeypatch.setattr(ps, "FREEZE_STATE_PATH", str(p))
    gov = _GovStub()
    import backend.services.runtime_governor as rg
    monkeypatch.setattr(rg, "runtime_governor", gov)
    ps._freezee_cache.update({"ts": 0.0, "expired": []})
    out = ps.parity_prune_expired(["scalp"])
    assert out == ["scalp"]
    assert gov.intents == []
