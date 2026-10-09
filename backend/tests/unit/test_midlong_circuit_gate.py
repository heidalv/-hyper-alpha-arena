# -*- coding: utf-8 -*-
"""[2026-08-29 全面修复] mid 层熔断闸（midlong_circuit_gate）契约测试。

数据依据：VELVET 8/12-13 两天 54 笔空 -410（无熔断裸奔）；mid 空头 30 天
-601.73 最差象限。契约：
  1. 连亏 N(默认3) 笔 → 冷却 12h 内禁开；
  2. 单 symbol 当日净亏 ≤ -cap → 熔断至次日；
  3. mid/long 新开空头由总开关控制：MIDLONG_OPEN_SHORT_ENABLED=true → 按
     MIDLONG_SHORT_MODE 策略放行；false → off（2026-09-27 用户指令撤销「不做空」封锁）；
  4. 多头正常放行、盈利重置连亏计数、状态落盘可恢复。
"""
import importlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


def _fresh_gate(monkeypatch, tmp_path, **env):
    for k, v in {
        "MIDLONG_CIRCUIT_ENABLED": "true",
        "MIDLONG_CIRCUIT_CONSEC_LOSSES": "3",
        "MIDLONG_CIRCUIT_COOLDOWN_S": "43200",
        "MIDLONG_CIRCUIT_DAILY_LOSS_CAP": "60",
        "MIDLONG_OPEN_SHORT_ENABLED": "true",
        # 显式声明前提，别继承 .env 的生产值：线上现为 true + regime_gated，
        # 而本文件测的是 conditional 下的分支契约，不写死会随运维改配置而红。
        "MIDLONG_SHORT_MODE": "conditional",
        # [2026-09-09 第十六轮] 多头治理已独立（MIDLONG_LONG_MODE）且默认 learned；
        # 本文件测的是熔断器机制本身，显式 allow_all 隔离 regime/learned 分支。
        "MIDLONG_LONG_MODE": "allow_all",
        # [2026-09-29 全面执行] 本文件测的是「亏损熔断机制本身」，与新入场边际闸
        # （edge_gate：方向配对/m 分数）无关——显式关闭，隔离真实行情数据干扰
        # （生产 .env 已开，测试若不 pin 会因 z_btc 方向拦而全红）。
        "MIDLONG_EDGE_GATE_ENABLED": "false",
    }.items():
        monkeypatch.setenv(k, str(env.get(k, v)))
    state_file = tmp_path / "midlong_circuit_state.json"
    mod = importlib.import_module("backend.services.full_auto.midlong_circuit_gate")
    # 先 reload（重读 env 常量），再 patch——reload 会重建模块全局，patch 必须在后
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_STATE_FILE", str(state_file))
    monkeypatch.setattr(mod, "_state", {})
    monkeypatch.setattr(mod, "_loaded", True)  # 跳过磁盘加载，用注入的空状态
    # [2026-09-11] 本文件测的是「亏损熔断机制本身」，须显式声明"非模拟账户"前提：
    # 生产里 paper 账户已按用户指令豁免一切亏损冻结（loss_lock_policy），
    # 不 pin 的话这些用例会因"豁免生效"而永远放行，测不到机制。
    import backend.services.risk_management.loss_lock_policy as _llp

    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: False)
    return mod


def test_consec_losses_ban_and_recovery(monkeypatch, tmp_path):
    mod = _fresh_gate(monkeypatch, tmp_path)
    for _ in range(2):
        ok, _ = mod.check_midlong_entry(14, "VELVET", side="long")
        assert ok
        mod.record_midlong_outcome(14, "VELVET", -10)
    # 连亏2笔未到阈值3
    ok, reason = mod.check_midlong_entry(14, "VELVET", side="long")
    assert ok
    mod.record_midlong_outcome(14, "VELVET", -10)
    # 连亏3笔 → 熔断
    ok, reason = mod.check_midlong_entry(14, "VELVET", side="long")
    assert not ok and "mid_circuit_banned" in reason
    # 状态已落盘（重启可恢复）
    assert os.path.exists(mod._STATE_FILE)
    # 熔断过期后自动解禁
    st = mod._state["14:VELVET"]
    st["banned_until"] = time.time() - 1
    ok, _ = mod.check_midlong_entry(14, "VELVET", side="long")
    assert ok


def test_daily_loss_cap(monkeypatch, tmp_path):
    mod = _fresh_gate(monkeypatch, tmp_path)
    mod.record_midlong_outcome(14, "KAITO", -40)
    ok, _ = mod.check_midlong_entry(14, "KAITO", side="long")
    assert ok  # -40 未到 -60 上限
    mod.record_midlong_outcome(14, "KAITO", -25)  # 日累计 -65
    ok, reason = mod.check_midlong_entry(14, "KAITO", side="long")
    assert not ok and "mid_circuit_banned" in reason


def test_short_conditional_mode(monkeypatch, tmp_path):
    """[2026-08-29 v2] conditional（默认）：无下行证据拦、有证据放、off 全停、on 无条件。"""
    mod = _fresh_gate(monkeypatch, tmp_path)

    def _repatch():
        # reload 会重建模块全局，monkeypatch 必须重新应用
        monkeypatch.setattr(mod, "_STATE_FILE", str(tmp_path / "midlong_circuit_state.json"))
        monkeypatch.setattr(mod, "_state", {})
        monkeypatch.setattr(mod, "_loaded", True)

    # 无证据 → 拦
    ok, reason = mod.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert not ok and "midlong_short_no_bias" in reason
    # 4h 偏空证据 → 放行
    ms = {"BTC": {"orchestrator": {"mid_bias": "bearish"}}}
    ok, _ = mod.check_midlong_entry(14, "BTC", side="sell", tier="mid", market_summary=ms)
    assert ok
    # 24h 跌 ≥2% 证据 → 放行
    ms2 = {"BTC": {"price_change_24h_pct": -0.03}}
    ok, _ = mod.check_midlong_entry(14, "BTC", side="sell", tier="mid", market_summary=ms2)
    assert ok
    # off 模式全停
    monkeypatch.setenv("MIDLONG_SHORT_MODE", "off")
    importlib.reload(mod)
    _repatch()
    ok, reason = mod.check_midlong_entry(14, "BTC", side="sell", tier="mid", market_summary=ms)
    assert not ok and "midlong_short_off" in reason
    monkeypatch.setenv("MIDLONG_SHORT_MODE", "conditional")
    importlib.reload(mod)
    _repatch()
    # on 模式（显式策略档）无条件放
    monkeypatch.setenv("MIDLONG_SHORT_MODE", "on")
    importlib.reload(mod)
    _repatch()
    ok, _ = mod.check_midlong_entry(14, "BTC", side="sell", tier="mid")
    assert ok


def test_win_resets_consec(monkeypatch, tmp_path):
    mod = _fresh_gate(monkeypatch, tmp_path)
    mod.record_midlong_outcome(14, "UNI", -10)
    mod.record_midlong_outcome(14, "UNI", -10)
    mod.record_midlong_outcome(14, "UNI", +5)  # 盈利重置
    mod.record_midlong_outcome(14, "UNI", -10)  # 连亏重新从 1 计
    ok, _ = mod.check_midlong_entry(14, "UNI", side="long")
    assert ok


def test_account_isolation(monkeypatch, tmp_path):
    mod = _fresh_gate(monkeypatch, tmp_path)
    for _ in range(3):
        mod.record_midlong_outcome(14, "BTC", -10)
    ok, _ = mod.check_midlong_entry(14, "BTC", side="long")
    assert not ok
    ok, _ = mod.check_midlong_entry(188, "BTC", side="long")
    assert ok  # 账户 188 不受账户 14 熔断影响


# ── [2026-09-11 用户指令] 模拟账户不做亏损冻结 ──
# 原话：「模拟账户本来就是收集交易数据，你还弄个极端亏损冻结？」
# 契约：paper（loss_lock_policy.loss_locks_disabled=True）时，
#   ① 连亏不再产生冷却；② 日亏帽不再熔断；③ 记账侧直接返回（不写状态）。

def test_paper_account_not_frozen_by_consec_losses(monkeypatch, tmp_path):
    """模拟账户**不因连亏被冻结**；但按 2026-09-19 用户口径**仍要记账**。

    [2026-09-24 R2 用例更新] 旧断言 `mod._state == {}`（"paper 不写状态"）编码的是
    2026-09-11 的旧口径，已被 **轮120 2026-09-19 用户拍板**推翻：
    「单币亏钱，就是冻结单个亏钱的币」⇒ 恢复记账（`MIDLONG_CIRCUIT_PAPER_LOCK` 默认
    true）。当时实测后果是状态文件 9 天没写（mtime 09-10 20:48）、UNI 连吃 3 个 SL
    也没得到单币冷却。现行契约：**记账照写、只按 (account,symbol) 粒度，
    不冻结 paper 开仓、不产生全局或跨币冻结**。回滚口径见下一条用例。
    """
    mod = _fresh_gate(monkeypatch, tmp_path)
    import backend.services.risk_management.loss_lock_policy as _llp

    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: True)
    for _ in range(5):  # 远超连亏阈值 3
        mod.record_midlong_outcome(14, "VELVET", -10)
    ok, reason = mod.check_midlong_entry(14, "VELVET", side="long")
    assert ok, f"模拟账户不应被连亏熔断: {reason}"
    assert "14:VELVET" in mod._state, "轮120 口径：paper 也要记单币冷却账"
    assert mod._state["14:VELVET"]["consec_losses"] == 5
    assert all(k == "14:VELVET" for k in mod._state), (
        f"只允许按 (account,symbol) 记账，不得产生全局/跨币状态: {list(mod._state)}"
    )
    ok2, _ = mod.check_midlong_entry(14, "KAITO", side="long")
    assert ok2, "另一个币不应被 VELVET 的连亏连带冻结"


def test_paper_circuit_rollback_switch_stops_recording(monkeypatch, tmp_path):
    """回滚口径：MIDLONG_CIRCUIT_PAPER_LOCK=false → 回到 09-11 的「paper 不记账」。

    注意：`_fresh_gate` 只转发它在默认表里声明过的键，所以本键必须在调用前先 setenv，
    才能被 `_fresh_gate` 内部的 `importlib.reload` 读进模块常量。
    """
    monkeypatch.setenv("MIDLONG_CIRCUIT_PAPER_LOCK", "false")
    mod = _fresh_gate(monkeypatch, tmp_path)
    import backend.services.risk_management.loss_lock_policy as _llp

    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: True)
    for _ in range(5):
        mod.record_midlong_outcome(14, "VELVET", -10)
    assert mod._state == {}, "回滚开关打开时 paper 记账侧应直接返回"


def test_paper_account_not_frozen_by_daily_loss_cap(monkeypatch, tmp_path):
    mod = _fresh_gate(monkeypatch, tmp_path)
    import backend.services.risk_management.loss_lock_policy as _llp

    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: True)
    mod.record_midlong_outcome(14, "KAITO", -500)  # 远超日亏帽 60
    ok, reason = mod.check_midlong_entry(14, "KAITO", side="long")
    assert ok, f"模拟账户不应被日亏帽熔断: {reason}"


def test_live_account_still_frozen(monkeypatch, tmp_path):
    """live（loss_locks_disabled=False）行为完全不变——冻结照常生效。"""
    mod = _fresh_gate(monkeypatch, tmp_path)
    import backend.services.risk_management.loss_lock_policy as _llp

    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: False)
    for _ in range(3):
        mod.record_midlong_outcome(188, "BTC", -10)
    ok, reason = mod.check_midlong_entry(188, "BTC", side="long")
    assert not ok and "mid_circuit_banned" in reason
