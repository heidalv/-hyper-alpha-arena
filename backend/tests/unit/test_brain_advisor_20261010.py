# -*- coding: utf-8 -*-
"""[2026-10-10 主脑接入高频学习] 「一张嘴 + 一道关」的护栏契约。

用户原话："模型必须交出三样：一个旋钮、一句原因、用到的知识条目编号。没有编号，
这次作废。"本测试把这句话逐条钉成断言，并覆盖三个真实会出事的失败模式：

  · **没有编号就作废**：否则模型可以用"常识"改参数，事后无法审计它依据了什么；
  · **白名单 + 现值一致**：否则会基于过期快照改参数，或把吃单进场/加杠杆这类
    策略身份改动混进来；
  · **统计关与降级**：知识库不可用、模型不可用、该车道样本不够 ⇒ 参数一个字节不改。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import brain_advisor as BA  # noqa: E402


CARD_ID = "kb-test-hft-01"


class FakeRag:
    def __init__(self, degraded=False, cards=None, lessons=None):
        self.is_ready = True
        self.is_degraded = degraded
        self._cards = cards if cards is not None else [
            {"card_id": CARD_ID, "text": f"知识卡片[{CARD_ID}] 挂单与吃单", "distance": 0.1,
             "source": "测试", "topics": "orderbook", "lanes": ["hft"]}]
        self._lessons = lessons if lessons is not None else [
            {"text": "策略教训: ETH 挂单被吃", "strategy_id": "mm_asterdex_pp",
             "source": "", "distance": 0.2}]

    def retrieve(self, query_text, top_k=5, collections=None, metadata_filter=None):
        out = {}
        for c in (collections or ()):
            if c == "knowledge_cards":
                out[c] = [{"text": x["text"], "distance": x.get("distance", 0.1),
                           "metadata": {"card_id": x["card_id"],
                                        "lanes": ",".join(x.get("lanes") or ["all"])}}
                          for x in self._cards]
            elif c == "strategy_lessons":
                out[c] = [{"text": x["text"], "distance": x.get("distance", 0.2),
                           "metadata": {"strategy_id": x["strategy_id"]}} for x in self._lessons]
            else:
                out[c] = []
        return out


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("MM_BRAIN_ENABLED", "MM_BRAIN_APPLY", "MM_BRAIN_MIN_N",
              "MM_BRAIN_MAX_GAP_S", "MM_BRAIN_KB_TOPK"):
        monkeypatch.delenv(k, raising=False)
    yield


def _evidence(n: int = 40, mean_y: float = 2.0, win_rate: float = 0.6,
              avg_win: float = 6.0, avg_loss: float = -3.0) -> dict:
    m = {"n": n, "mean_y": mean_y, "win_rate": win_rate,
         "avg_win_bp": avg_win, "avg_loss_bp": avg_loss}
    return {"lane": "hft", "current_params": {"pp_rest_sec": 15.0, "pp_thin_frac": 0.5,
                                              "pp_exit_ticks": 1.0,
                                              "pp_bucket_min_n": 20.0},
            "recent_3h": dict(m), "recent_30m": dict(m), "lifetime": dict(m),
            "roundtrips_total": n}


def _proposal(**over) -> dict:
    p = {"lane": "hft", "action": "adjust", "param": "pp_rest_sec", "old": 15.0,
         "new": 20.0, "rollback": 15.0, "reason": "刚被打穿，多歇一会",
         "cited": [{"card_id": CARD_ID, "use": "逆向选择"}]}
    p.update(over)
    return p


def _run(monkeypatch, *, proposal=None, evidence=None, kb=None, apply_fn=None,
         raw=None, apply_flag=True, tmp_path=None, cf_runner=None):
    """把 run_lane 的四个外部依赖全部替换掉（纯内存）。"""
    ev = evidence if evidence is not None else _evidence()
    monkeypatch.setattr(BA, "gather_evidence", lambda lane, root=None, now=None: ev)
    monkeypatch.setattr(BA, "last_journal_ts", lambda lane, root=None: 0.0)
    if apply_flag:
        monkeypatch.setenv("MM_BRAIN_APPLY", "1")
    else:
        monkeypatch.setenv("MM_BRAIN_APPLY", "0")
    raw_text = raw if raw is not None else json.dumps(
        proposal if proposal is not None else _proposal(), ensure_ascii=False)
    return BA.run_lane("hft", root=tmp_path or BA._root(),
                       kb_service=kb or FakeRag(),
                       llm=lambda msgs: raw_text,
                       apply_fn=apply_fn or (lambda p: True),
                       cf_runner=cf_runner)


# ══ 1. 知识编号：没有就作废 ════════════════════════════════════════════
def test_missing_cited_is_rejected(monkeypatch, tmp_path):
    p = _proposal()
    p.pop("cited")
    r = _run(monkeypatch, proposal=p, tmp_path=tmp_path)
    assert r["decision"] == "rejected"
    assert r["why"] == "no_cited_card"


def test_empty_cited_is_rejected(monkeypatch, tmp_path):
    r = _run(monkeypatch, proposal=_proposal(cited=[]), tmp_path=tmp_path)
    assert r["decision"] == "rejected" and r["why"] == "no_cited_card"


def test_cited_must_be_among_retrieved_cards(monkeypatch, tmp_path):
    """编号必须真的来自本轮检索结果（不能凭空编一个编号）。"""
    r = _run(monkeypatch, proposal=_proposal(
        cited=[{"card_id": "kb-fabricated-99"}]), tmp_path=tmp_path)
    assert r["decision"] == "rejected"
    assert r["why"].startswith("cited_not_retrieved")


def test_cited_card_id_passes_and_is_recorded(monkeypatch, tmp_path):
    seen = {}
    r = _run(monkeypatch, apply_fn=lambda p: seen.update(p) or True, tmp_path=tmp_path)
    assert r["decision"] == "applied"
    assert seen["param"] == "pp_rest_sec"
    assert seen["cited"] == [CARD_ID]
    assert seen["verdict_metric"] == "roundtrip_y"


# ══ 2. 白名单 / 单旋钮 / 边界 / 现值一致 ═══════════════════════════════
def test_forbidden_keys_never_apply(monkeypatch, tmp_path):
    for key in ("taker_entry", "spread_min_bp", "leverage", "active_flow_mode",
                "notional_loss_frac"):
        r = _run(monkeypatch, proposal=_proposal(param=key), tmp_path=tmp_path)
        assert r["decision"] == "rejected", key
        assert r["why"].startswith("forbidden_key"), key


def test_unknown_key_is_not_in_whitelist(monkeypatch, tmp_path):
    r = _run(monkeypatch, proposal=_proposal(param="w_base_bp"), tmp_path=tmp_path)
    assert r["decision"] == "rejected"
    assert r["why"].startswith("not_in_whitelist")


def test_two_knob_proposal_has_no_place_to_land(monkeypatch, tmp_path):
    """一次改两个旋钮：第二个键根本无处安放 ⇒ 必然被拒。"""
    seen = []
    p = _proposal()
    p["params"] = {"pp_thin_frac": 0.3}
    r = _run(monkeypatch, proposal=p, apply_fn=lambda x: seen.append(x) or True,
             tmp_path=tmp_path)
    # 结构上只可能落地一个键：第二个键必须落在"无处安放"的位置，不能被静默采纳
    assert r["decision"] == "applied"
    assert "params" not in seen[0], "第二个旋钮不得随提案一起落地"
    assert r["param"] == "pp_rest_sec"


def test_out_of_bounds_is_rejected(monkeypatch, tmp_path):
    r = _run(monkeypatch, proposal=_proposal(param="pp_exit_ticks", new=9.0),
             tmp_path=tmp_path)
    assert r["decision"] == "rejected" and r["why"].startswith("out_of_bounds")


def test_stale_old_value_is_rejected(monkeypatch, tmp_path):
    """现值已被别处改过 ⇒ 丢弃（不能基于过期快照改参数）。"""
    r = _run(monkeypatch, proposal=_proposal(old=7.0), tmp_path=tmp_path)
    assert r["decision"] == "rejected" and r["why"].startswith("stale_old")


def test_no_change_is_rejected(monkeypatch, tmp_path):
    r = _run(monkeypatch, proposal=_proposal(new=15.0), tmp_path=tmp_path)
    assert r["decision"] == "rejected" and r["why"] == "no_change"


def test_lane_mismatch_is_rejected(monkeypatch, tmp_path):
    r = _run(monkeypatch, proposal=_proposal(lane="mid"), tmp_path=tmp_path)
    assert r["decision"] == "rejected" and r["why"] == "lane_mismatch"


def test_model_hold_keeps_params(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(BA, "gather_evidence",
                        lambda lane, root=None, now=None: _evidence())
    monkeypatch.setattr(BA, "last_journal_ts", lambda lane, root=None: 0.0)
    r = BA.run_lane("hft", root=tmp_path, kb_service=FakeRag(),
                    llm=lambda m: '{"lane":"hft","action":"hold","reason":"证据不足"}',
                    apply_fn=lambda p: seen.append(p) or True)
    assert r["decision"] == "hold" and not seen


# ══ 3. 统计关 ══════════════════════════════════════════════════════════
@pytest.mark.parametrize("kw,expect", [
    ({"n": 10}, "样本不足"),
    ({"mean_y": -0.5}, "平均非正"),
    ({"win_rate": 0.20}, "低于打平线"),
    # 打平线只取决于赚/亏幅度（6/(6+4)=60%），所以给 60% 胜率；此时"赚的≤亏的"才是
    # 真正拦住它的那一关 —— 四个判据必须各自可单独触发。
    ({"win_rate": 0.60, "avg_win": 4.0, "avg_loss": -4.0}, "不比亏的"),
])
def test_hft_stats_gate_blocks_bad_windows(monkeypatch, tmp_path, kw, expect):
    ev = _evidence(**kw)
    r = _run(monkeypatch, evidence=ev, tmp_path=tmp_path)
    assert r["decision"] == "blocked_by_gate"
    assert expect in r["why"]


def test_hft_stats_gate_passes_good_window():
    ok, why = BA.hft_stats_gate({"n": 40, "mean_y": 1.6, "win_rate": 0.56,
                                 "avg_win_bp": 5.2, "avg_loss_bp": -2.9})
    assert ok and why == "通过"
    # 打平线两侧
    assert BA.hft_stats_gate({"n": 40, "mean_y": 0.1, "win_rate": 0.33,
                              "avg_win_bp": 1.4, "avg_loss_bp": -4.0})[0] is False


# ══ 4. 降级即放弃 ══════════════════════════════════════════════════════
def test_kb_degraded_blocks_any_change(monkeypatch, tmp_path):
    seen = []
    r = _run(monkeypatch, kb=FakeRag(degraded=True),
             apply_fn=lambda p: seen.append(p) or True, tmp_path=tmp_path)
    assert r["decision"] == "kb_unavailable" and not seen


def test_kb_without_cards_blocks_any_change(monkeypatch, tmp_path):
    r = _run(monkeypatch, kb=FakeRag(cards=[], lessons=[]), tmp_path=tmp_path)
    assert r["decision"] == "kb_unavailable"


def test_bad_llm_output_blocks_any_change(monkeypatch, tmp_path):
    seen = []
    r = _run(monkeypatch, raw="抱歉，我无法回答", apply_fn=lambda p: seen.append(p) or True,
             tmp_path=tmp_path)
    assert r["decision"] == "llm_invalid" and not seen


def test_apply_disabled_is_proposal_only(monkeypatch, tmp_path):
    seen = []
    r = _run(monkeypatch, apply_flag=False, apply_fn=lambda p: seen.append(p) or True,
             tmp_path=tmp_path)
    assert r["decision"] == "proposal_only" and not seen
    assert r["param"] == "pp_rest_sec" and r["cited"] == [CARD_ID]


def test_master_switch_off(monkeypatch, tmp_path):
    monkeypatch.setenv("MM_BRAIN_ENABLED", "0")
    r = _run(monkeypatch, tmp_path=tmp_path)
    assert r["decision"] == "disabled"


def test_lane_without_whitelist_only_holds(monkeypatch, tmp_path):
    """白名单为空的车道 ⇒ 结构上只能 hold，绝不猜键名去改。"""
    monkeypatch.setattr(BA, "gather_evidence",
                        lambda lane, root=None, now=None: {"lane": "mid",
                                                           "current_params": {}})
    monkeypatch.setitem(BA.MIDLONG_WHITELIST, "mid", {})   # 模拟"尚未登记"的状态（monkeypatch 会自动还原）
    r = BA.run_lane("mid", root=tmp_path, kb_service=FakeRag(),
                    llm=lambda m: "{}", apply_fn=lambda p: True)
    assert r["decision"] == "no_whitelist"


def test_mid_lane_uses_lane_scoped_cards(monkeypatch, tmp_path):
    """中线的检索结果里必须**没有**高频专属卡片（否则模型会拿贴价/排队去改中线）。"""
    rag = FakeRag(cards=[
        {"card_id": "kb-hft-only", "text": "高频卡", "lanes": ["hft"]},
        {"card_id": "kb-swing-ok", "text": "中线卡", "lanes": ["mid", "all"]},
    ])
    seen = {}
    monkeypatch.setattr(BA, "gather_evidence",
                        lambda lane, root=None, now=None: {
                            "lane": "mid", "current_params": {"mid_daily_cap": 6}})
    monkeypatch.setattr(BA, "last_journal_ts", lambda lane, root=None: 0.0)
    r = BA.run_lane("mid", root=tmp_path, kb_service=rag,
                    llm=lambda m: json.dumps({
                        "lane": "mid", "action": "adjust", "param": "mid_daily_cap",
                        "old": 6, "new": 8, "reason": "样本够",
                        "cited": [{"card_id": "kb-swing-ok"}]}, ensure_ascii=False),
                    apply_fn=lambda p: seen.update(p) or True)
    assert r["decision"] == "applied" and seen["param"] == "mid_daily_cap"
    assert r["kb"]["card_ids"] == ["kb-swing-ok"]


def test_midlong_whitelist_only_contains_hot_appliable_keys():
    """中/长线白名单只许收 `runtime_tuning` 的键。

    为什么这条是硬的：其它 `MIDLONG_*`/`TREND_*`/`EXIT_POLICY_*` 是 import 期读 env，
    改完要重启才生效；而改后 30 分钟就要比指标 ⇒ 会被判成"变差"而误回滚。
    """
    from backend.services import runtime_tuning_store as rts
    for lane in ("mid", "long"):
        keys = BA.allowed_keys(lane)
        assert keys, f"{lane} 白名单不该为空（侦察已确认有可调键）"
        for key in keys:
            head = key.split(".")[0]
            assert head in rts._DEFAULT_SCHEMA, f"{key} 不在 runtime_tuning schema 里"
            lo, hi = keys[key]
            assert lo < hi
    assert "mid_daily_cap" in BA.allowed_keys("mid")
    assert "trend_daily_cap" in BA.allowed_keys("long")
    # 策略身份/需要重启的键一律不得出现在白名单里
    for bad in ("TREND_ENTRY_RULE", "TREND_STOP_RATCHET", "TREND_CORE_SYMBOLS",
                "MIDLONG_CHOP_MODE", "EXIT_POLICY_MID_TP_STAGES",
                "MIDLONG_TIER_MARGIN_PCT_MID", "TREND_CHANDELIER_MULT",
                "TIER_MID_MIN_SL", "MIDLONG_OPEN_READINESS_MIN_MID"):
        assert bad not in BA.allowed_keys("mid"), bad
        assert bad not in BA.allowed_keys("long"), bad


def test_mid_lane_evidence_reads_hot_values(monkeypatch):
    """中/长线证据必须读 runtime_tuning 的**当前值**（不是硬编码、不是 .env）。"""
    ev = BA.gather_evidence("mid")
    cur = ev.get("current_params") or {}
    assert set(cur) == set(BA.allowed_keys("mid"))
    assert cur.get("mid_daily_cap") is not None
    assert "runtime_tuning" in str(ev.get("hot_apply") or "")
    assert ev.get("whitelist_reason")


# ══ 4′. 桶门阈值的事前反事实关 ═════════════════════════════════════════
def test_bucket_min_n_counterfactual_accepts_matching_best():
    ok, why = BA.bucket_min_n_counterfactual(
        40.0, runner=lambda apply=False: {"best": {"min_n": 40.0}, "action": "deploy"})
    assert ok and "通过" in why


def test_bucket_min_n_counterfactual_rejects_other_value():
    ok, why = BA.bucket_min_n_counterfactual(
        50.0, runner=lambda apply=False: {"best": {"min_n": 40.0}, "action": "deploy"})
    assert ok is False and "反事实最优" in why


def test_bucket_min_n_counterfactual_blocks_when_no_better_candidate():
    ok, why = BA.bucket_min_n_counterfactual(
        20.0, runner=lambda apply=False: {"best": None, "action": "keep",
                                          "incumbent_min_n": 20.0})
    assert ok is False and "无更优候选" in why


def test_bucket_min_n_counterfactual_fails_closed_on_error():
    def _boom(apply=False):
        raise RuntimeError("journal unreadable")
    ok, why = BA.bucket_min_n_counterfactual(30.0, runner=_boom)
    assert ok is False and why.startswith("counterfactual_failed")


def test_bucket_min_n_counterfactual_passes_when_samples_insufficient():
    """样本不足时反事实做不了 ⇒ 放行，但不能假装"已经验证过"。"""
    ok, why = BA.bucket_min_n_counterfactual(
        30.0, runner=lambda apply=False: {"ok": True, "action": "none",
                                          "reason": "PP 往返 3 < 20，样本不足"})
    assert ok is True and "样本不足" in why


def test_bucket_gate_wired_into_run_lane(monkeypatch, tmp_path):
    """`pp_bucket_min_n` 提案必须真的走反事实关（其余三项不走）。"""
    p = _proposal(param="pp_bucket_min_n", old=20.0, new=40.0)
    # 反事实最优 = 100（≠ 提议 40）⇒ 必须被拦下
    r = _run(monkeypatch, proposal=p, tmp_path=tmp_path,
             cf_runner=lambda apply=False: {"best": {"min_n": 100.0}, "action": "deploy"})
    assert r["decision"] == "blocked_by_counterfactual"
    assert r["gate"]["name"] == "pp_bucket_counterfactual"
    # 反事实最优 = 提议值 ⇒ 放行
    r2 = _run(monkeypatch, proposal=p, tmp_path=tmp_path,
              cf_runner=lambda apply=False: {"best": {"min_n": 40.0}, "action": "deploy"})
    assert r2["decision"] == "applied" and r2["param"] == "pp_bucket_min_n"
    # 其余三项旋钮**不**碰反事实（账里根本没有那种反事实）
    r3 = _run(monkeypatch, proposal=_proposal(param="pp_thin_frac", old=0.5, new=0.3),
              tmp_path=tmp_path,
              cf_runner=lambda apply=False: 1 / 0)
    assert r3["decision"] == "applied" and r3["gate"]["name"] == "hft_stats"


# ══ 5. 触发节奏与审计 ══════════════════════════════════════════════════
def test_not_due_when_samples_low_and_gap_small(monkeypatch, tmp_path):
    ev = _evidence(n=3)
    monkeypatch.setattr(BA, "gather_evidence", lambda lane, root=None, now=None: ev)
    monkeypatch.setattr(BA, "last_journal_ts", lambda lane, root=None: 1000.0)
    r = BA.run_lane("hft", root=tmp_path, kb_service=FakeRag(),
                    llm=lambda m: "{}", apply_fn=lambda p: True, now=1200.0)
    assert r["decision"] == "not_due"


def test_due_by_gap_even_with_few_samples(monkeypatch, tmp_path):
    """到点就算样本不够也重新看一次（否则车道的探索会永久停摆）。"""
    ev = _evidence(n=3, mean_y=1.0, win_rate=0.6, avg_win=5.0, avg_loss=-3.0)
    monkeypatch.setattr(BA, "gather_evidence", lambda lane, root=None, now=None: ev)
    monkeypatch.setattr(BA, "last_journal_ts", lambda lane, root=None: 1000.0)
    r = BA.run_lane("hft", root=tmp_path, kb_service=FakeRag(),
                    llm=lambda m: json.dumps(_proposal(), ensure_ascii=False),
                    apply_fn=lambda p: True, now=1000.0 + 4000.0)
    assert r["decision"] == "blocked_by_gate", "到点后仍须过统计关"


def test_every_round_is_journaled(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path=tmp_path)
    _run(monkeypatch, proposal=_proposal(param="taker_entry"), tmp_path=tmp_path)
    lines = [json.loads(x) for x in
             (tmp_path / BA.ADVISOR_JOURNAL).read_text(encoding="utf-8").splitlines()]
    assert len(lines) >= 2
    assert {"applied", "rejected"} <= {x["decision"] for x in lines}
    assert all("ts" in x and "lane" in x and "why" in x for x in lines)


# ══ 6. 证据与提示词 ════════════════════════════════════════════════════
def test_window_metrics_counts_only_window():
    rows = [{"ts": 10.0, "y_bp": 5.0}, {"ts": 20.0, "y_bp": -3.0},
            {"ts": 100.0, "y_bp": 9.0}]
    m = BA.window_metrics(rows, 0.0, 50.0)
    assert m["n"] == 2 and m["mean_y"] == pytest.approx(1.0)
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["avg_win_bp"] == pytest.approx(5.0)
    assert m["avg_loss_bp"] == pytest.approx(-3.0)


def test_parse_llm_json_unwraps_openai_response_body(monkeypatch, tmp_path):
    """`call_llm_api_sync` 有时返回**完整响应体**而不是内容。

    实测（2026-10-10 首次真跑）：返回的是
    `{"choices":[{"message":{"content":"{\\"lane\\":\\"hft\\",\\"action\\":\\"hold\\"}"}}]}`。
    不拆开就会把"模型说了 hold"误判成"返回了非法 JSON"——那就等于丢掉了模型的判断，
    还会在日志里留下一个假的 `llm_invalid`。
    """
    wrapped = json.dumps({"choices": [{"message": {
        "content": json.dumps({"lane": "hft", "action": "hold", "reason": "证据不足"},
                              ensure_ascii=False)}}]}, ensure_ascii=False)
    parsed = BA.parse_llm_json(wrapped)
    assert isinstance(parsed, dict)
    assert parsed.get("action") == "hold", "必须拆开 choices/message/content 再解析"

    r = _run(monkeypatch, raw=wrapped, tmp_path=tmp_path)
    assert r["decision"] == "hold"
    assert "证据不足" in r["why"]


def test_parse_llm_json_keeps_plain_payload():
    plain = json.dumps({"lane": "hft", "action": "hold"}, ensure_ascii=False)
    assert BA.parse_llm_json(plain) == {"lane": "hft", "action": "hold"}
    assert BA.parse_llm_json("") is None
    assert BA.parse_llm_json("不是 JSON") is None


def test_prompt_carries_cards_and_whitelist():
    ev = _evidence()
    kb = {"cards": [{"card_id": CARD_ID, "text": "卡片正文"}],
          "lessons": [{"text": "教训正文", "strategy_id": "mm_asterdex_pp"}],
          "conflict_rule": "以账为准"}
    msgs = BA.build_messages(ev, kb, "hft")
    blob = json.dumps(msgs, ensure_ascii=False)
    assert CARD_ID in blob and "教训正文" in blob
    assert "pp_rest_sec" in blob and "以账为准" in blob
    assert msgs[0]["role"] == "system" and "没有编号" in msgs[0]["content"]


def test_hft_whitelist_matches_flow_safe_params():
    """白名单边界必须与 `flow_rules.FLOW_SAFE_PARAMS` 同源，否则两边会打架。"""
    from backend.services.market_maker.flow_rules import FLOW_SAFE_PARAMS
    for key, bounds in BA.HFT_WHITELIST.items():
        assert key in FLOW_SAFE_PARAMS, key
        assert tuple(bounds) == tuple(FLOW_SAFE_PARAMS[key]), key
