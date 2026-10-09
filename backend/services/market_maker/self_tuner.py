# -*- coding: utf-8 -*-
"""[h706 2026-10-02] 做市模块自进化器(self_tuner)。

用户最终定案(2026-10-02 12:5x):**直接用 DSH,不走项目侧 LLM API**;
DSH 会话所用模型 = **deepseek v41 flash**。

## 最终架构(DSH 桥为主,零项目 API 依赖)

    证据收集(变量) → 写 logs/self_tuner_review.json
        └─► DSH(本会话定时提醒,每 2h)读取 → 推理(deepseek v41 flash)
            → 按护栏落地(mm_apply_params 单变量 + sync_live_cfg + 登记 pending)
        └─► scripts/self_tuner_verdict.py(每小时)比对改前改后,不达标自动回滚

    --llm 直连(.env DEEPSEEK_API_KEY)保留为可选自主模式,默认关闭。

护栏(绝不裸奔):白名单 14 个数值参数+边界;单变量;old/new/rollback 齐全;
证据不足(n<50)⇒ 不写请求;快判自动回滚(改后 < 改前×0.8)。

用法:
    python -m backend.services.market_maker.self_tuner --hours 6     # 默认:写 DSH 桥请求
    python -m backend.services.market_maker.self_tuner --hours 6 --llm --apply  # 可选:直连自主
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
PENDING = ROOT / "logs" / "self_tuner_pending.json"
REVIEW = ROOT / "logs" / "self_tuner_review.json"
# [h707] 演化记忆:玩法手册(定律+证据+翻转史)与经验账本(每次决策+结果)
PLAYBOOK = ROOT / "data" / "self_tuner_playbook.json"
EXPERIENCE = ROOT / "data" / "self_tuner_experience.jsonl"


def load_playbook() -> Dict[str, Any]:
    try:
        return json.loads(PLAYBOOK.read_text(encoding="utf-8"))
    except Exception:
        return {"laws": []}


def load_experience(limit: int = 10) -> List[Dict[str, Any]]:
    """[h707] 最近的经验样本(决策+结果),作为桥接复核的 few-shot 记忆。"""
    try:
        lines = EXPERIENCE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out

# [h706] 白名单:只允许这些**数值型单参数**,且必须落在边界内。
# 刻意排除:dict(per_symbol_spread_mult)、实盘上限(live_caps)、开关(fusion/mode)。
SAFE_PARAMS: Dict[str, tuple] = {
    "stop_loss_bp": (30.0, 100.0),
    "stop_taker_bp": (60.0, 200.0),
    "timeout_hard_taker_sec": (300.0, 1800.0),
    "take_profit_bp": (15.0, 80.0),
    "trail_lock_bp": (10.0, 40.0),
    "spread_mult": (0.5, 2.0),
    "max_width_bp": (1.0, 6.0),
    "min_edge_frac": (0.8, 2.5),
    "vpin_pause_threshold": (0.5, 0.95),
    "trend_pause_bp": (8.0, 30.0),
    "jump_pause_bp": (8.0, 40.0),
    "vol_pause_mult": (1.5, 5.0),
    "vol_pause_sigma": (1.5, 5.0),
    "compound_ratio": (0.1, 0.4),   # 仅 paper 车道(实盘强制 0,应用后同步会覆盖)
}

# 主动流白名单。做市挂宽、库存、compound_ratio、吃单超时不在其中。
from backend.services.market_maker.flow_rules import (  # noqa: E402
    FLOW_SAFE_PARAMS,
    experience_for_prompt,
    load_learn_params,
    playbook_for_prompt,
    save_learn_params,
)

SYSTEM_PROMPT_FLOW = (
    "你是高频车道（重复来回做市 ping-pong）的参数顾问。Aster 永续挂单手续费为 0。"
    "机器不猜涨跌：空仓挂买一卖一后面一档、成交后只挂反向平仓、穿透前撤进场单。"
    "每次只改一个白名单参数，给出 old/new/rollback。证据不足就回答 hold。\n"
    "禁止建议：挂宽、库存系数、compound_ratio、交易所杠杆、吃单超时、方向选边。\n"
    "判据是 rt_bp（开仓价→平仓价）的成功率与赚亏幅度，不是每小时成交笔数。"
    "笔数变多但平均变差视为失败。胜率跌破打平线或赚的没有亏的大就要收手。\n"
    "只输出 JSON:"
    "{\"action\":\"adjust\",\"param\":\"...\",\"old\":0,\"new\":0,\"rollback\":0,"
    "\"reason\":\"...\",\"verdict_metric\":\"roundtrip_y\",\"verdict_rule\":\"...\"}"
    " 或 {\"action\":\"hold\",\"reason\":\"...\"}"
)

SYSTEM_PROMPT = (
    "你是高频做市车道的参数调优顾问。根据给定证据(真实成交账本的归因统计)与当前参数,"
    "提出**至多一个**参数调整建议。铁律:\n"
    "1. 每次只允许改一个参数,且必须给出 old/new/rollback(用于自动回滚);\n"
    "2. 只允许白名单参数与给定边界,不得提议其他参数或复合改动;\n"
    "3. 调整必须能被证据支持(引用表里的数字),禁止凭空猜测;\n"
    "4. 该策略的已知盈利定律:捕获<0.5bp 的贴价成交亏损、≥0.5bp 盈利;"
    "入场腿赚钱、止损腿是主要亏损源(震荡市止损太紧会砍在恢复中);方向 fade(逆势)有效;\n"
    "5. 证据不足或没有明确改进方向时输出 {\"action\":\"hold\"},宁可不改;\n"
    "6. 只输出 JSON,不要输出任何其他内容。"
    "JSON 格式:"
    "{\"action\":\"adjust\",\"param\":\"...\",\"old\":0,\"new\":0,\"rollback\":0,"
    "\"reason\":\"...\",\"verdict_metric\":\"net_bp_per_leg\"|\"stop_loss_total\"|"
    "\"fills_per_hour\",\"verdict_rule\":\"...\"} 或 {\"action\":\"hold\",\"reason\":\"...\"}"
)


def _read_env_dsn() -> str:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def _db_row(sql: str, params: tuple = ()) -> Optional[tuple]:
    import psycopg
    try:
        with psycopg.connect(_read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    except Exception:
        return None


def _db_rows(sql: str, params: tuple = ()) -> list:
    import psycopg
    try:
        with psycopg.connect(_read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception:
        return []


def collect_evidence(hours: float = 6.0) -> Dict[str, Any]:
    """[h706] 证据摘要(提示词里的"变量")。全部来自真实账本 + 注册表。"""
    from datetime import datetime, timedelta, timezone as _tz
    day = "ts > date_trunc('day', now() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'"
    # psycopg3 下 `make_interval(hours => %s)` 不可靠 ⇒ 用 Python 算截止时刻
    _cutoff = (datetime.now(_tz.utc) - timedelta(hours=float(hours))).isoformat()
    win = "ts >= %s"
    lane = LANE
    ev: Dict[str, Any] = {"hours": hours, "lane": lane}

    r = _db_row(f"SELECT count(*), SUM(net_bp*notional)/10000.0, AVG(net_bp),"
                f" SUM(spread_bp*notional)/10000.0, SUM(price_bp*notional)/10000.0,"
                f" SUM(net_bp*notional)/NULLIF(SUM(notional),0)"
                f" FROM lane_ledger WHERE lane_id=%s AND event='fill' AND {win}",
                (lane, _cutoff))
    # ── [整顿轮·T21 2026-10-06] 明确标注**口径**，不再混用 ──
    #
    # 病根：本函数的判据提示词自己写着「判据是真成交往返的**平均可执行盈亏**，
    # 不是每小时成交笔数；笔数变多但平均变差视为失败」——
    # 但原实现把两种口径混在同一份报告里，且字段名不说明用的是哪种：
    #   · `net_usd` / `spread_usd` / `price_usd` = **名义加权**（SUM(x*notional)）
    #   · `net_bp_per_leg`                       = **简单平均**（AVG(net_bp)）
    # 名义分布极偏（实测单腿 $15 到 $68,247）⇒ 两者会给出**相反结论**：
    #   实测 6h 窗口 net_usd = **+$235.69**，而 net_bp_per_leg = **−0.195bp**。
    # 复核方看到这种报告极易误判。
    #
    # 修法：① 每个字段标注口径；② 补一个**名义加权**的每腿净额
    #       （`net_bp_weighted`）供直接对照。
    def _f(v: Any) -> float:
        try:
            return float(v or 0.0)
        except (TypeError, ValueError):
            return 0.0

    if r:
        _notl = 0.0
        _w = _f(r[5]) if len(r) > 5 else 0.0
        ev["window"] = {
            "legs": int(r[0] or 0),
            "net_usd": round(_f(r[1]), 4),
            "net_bp_per_leg": round(_f(r[2]), 3),          # 简单平均
            "net_bp_weighted": round(_w, 3),               # 名义加权
            "spread_usd": round(_f(r[3]), 4),
            "price_usd": round(_f(r[4]), 4),
            "basis_note": ("net_usd/spread_usd/price_usd/net_bp_weighted 为名义加权；"
                           "net_bp_per_leg 为简单平均。名义分布极偏时两者可能反号，"
                           "判断'每腿质量'请用 net_bp_weighted。"),
        }
        del _notl
    else:
        ev["window"] = {}

    r = _db_row(f"SELECT SUM(net_bp*notional)/10000.0, SUM(spread_bp*notional)/10000.0,"
                f" SUM(price_bp*notional)/10000.0 FROM lane_ledger"
                f" WHERE lane_id=%s AND event='fill' AND {day}", (lane,))
    ev["day"] = {"net_usd": round(float(r[0] or 0), 4), "spread_usd": round(float(r[1] or 0), 4),
                 "price_usd": round(float(r[2] or 0), 4)} if r else {}

    bands: Dict[str, Any] = {}
    for label, cond in (("cap_lt_0.5", "spread_bp < 0.5"),
                        ("cap_0.5_1", "spread_bp >= 0.5 AND spread_bp < 1.0"),
                        ("cap_ge_1", "spread_bp >= 1.0")):
        r = _db_row(f"SELECT count(*), SUM(net_bp*notional)/10000.0, AVG(net_bp)"
                    f" FROM lane_ledger WHERE lane_id=%s AND event='fill' AND {win}"
                    f" AND COALESCE(meta_json->>'exit_path','')='' AND {cond}",
                    (lane, _cutoff))
        if r and r[0]:
            bands[label] = {"n": int(r[0]), "net_usd": round(float(r[1] or 0), 4),
                            "net_bp": round(float(r[2] or 0), 3)}
    ev["capture_bands_entries"] = bands

    rows = _db_rows(f"SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'entry') ep,"
                    f" count(*), SUM(net_bp*notional)/10000.0, AVG(net_bp)"
                    f" FROM lane_ledger WHERE lane_id=%s AND event='fill' AND {win}"
                    f" GROUP BY 1 ORDER BY 3", (lane, _cutoff))
    ev["exit_attribution"] = [
        {"exit_path": r[0], "n": int(r[1]), "net_usd": round(float(r[2] or 0), 4),
         "net_bp": round(float(r[3] or 0), 3)} for r in rows]

    stops = _db_row(f"SELECT count(*), AVG(net_bp) FROM lane_ledger WHERE lane_id=%s"
                    f" AND event='fill' AND {win}"
                    f" AND COALESCE(meta_json->>'exit_path','') LIKE '%%stop%%'",
                    (lane, _cutoff))
    if stops and stops[0]:
        ev["stops"] = {"n": int(stops[0]), "avg_net_bp": round(float(stops[1] or 0), 3),
                       "per_hour": round(float(stops[0]) / max(1.0, float(hours)), 2)}

    try:
        from backend.services import lane_registry as reg
        meta = (reg.get_lane(LANE) or {}).get("meta") or {}
        params = dict(meta.get("params") or {})
        active = float(params.get("active_flow_mode") or 0.0) > 0
        ev["strategy"] = "active_flow" if active else "mm"
        if active:
            ev["current_params"] = load_learn_params(ROOT)
            ev["allowed_params"] = sorted(FLOW_SAFE_PARAMS)
        else:
            ev["current_params"] = {k: params.get(k) for k in SAFE_PARAMS}
        ev["universe"] = meta.get("symbols")
    except Exception:
        ev["current_params"] = {}
        ev["universe"] = []
    return ev


def build_messages(ev: Dict[str, Any]) -> List[Dict[str, str]]:
    prompt = SYSTEM_PROMPT_FLOW if ev.get("strategy") == "active_flow" else SYSTEM_PROMPT
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content":
            "当前参数:\n" + json.dumps(ev.get("current_params") or {}, ensure_ascii=False)
            + "\n\n证据摘要(近 "
            + str(ev.get("hours")) + " 小时):\n"
            + json.dumps({k: v for k, v in ev.items()
                          if k != "current_params"}, ensure_ascii=False, indent=2)},
    ]


def call_llm(messages: List[Dict[str, str]]) -> Optional[str]:
    """[h706b 2026-10-02] 通道 A:项目自有 DeepSeek(env 直连)。

    用户指正:项目本身就由 DeepSeek 驱动(.env 有 DEEPSEEK_API_KEY)——
    此前我走租户门控的 `get_llm_config_for_usage` 被"无账户/租户"拒绝,是错的。
    本项目做市模块的既有约定(researcher.py / direction_card.py)就是**直接用
    DEEPSEEK_* 环境变量构造 LLMConfig**,不经过配置表。照做。
    """
    import os
    try:
        from backend.services.llm_config_service import LLMConfig, call_llm_api_sync
    except Exception:
        return None
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    cfg = LLMConfig(
        id=0, name="mm-self-tuner", provider="deepseek",
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash",
        base_url=(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
                  or "https://api.deepseek.com"),
        api_key=key,
    )
    try:
        resp = call_llm_api_sync(config=cfg, messages=messages, temperature=0.2,
                                 max_tokens=600,
                                 response_format={"type": "json_object"},
                                 timeout=120.0, caller="mm_param_advisor")
        if isinstance(resp, dict):
            return resp.get("content") or resp.get("text") or json.dumps(resp)
        return str(resp or "")
    except Exception:
        return None


def parse_proposal(text: str, allowed: Optional[Dict[str, tuple]] = None) -> Optional[Dict[str, Any]]:
    """严格解析 + 护栏校验;任何不合规 ⇒ None(写 DSH 复核请求)。"""
    whitelist = allowed if allowed is not None else SAFE_PARAMS
    if not text:
        return None
    t = text.strip()
    if "```" in t:
        t = t.split("```")[1].split("```")[0].strip()
        if t.startswith("json"):
            t = t[4:].strip()
    try:
        p = json.loads(t)
    except Exception:
        return None
    if p.get("action") == "hold":
        return {"action": "hold", "reason": str(p.get("reason") or "")[:200]}
    if p.get("action") != "adjust":
        return None
    param = str(p.get("param") or "")
    if param not in whitelist:
        return None
    try:
        new = float(p["new"]); old = float(p["old"]); rollback = float(p["rollback"])
    except Exception:
        return None
    lo, hi = whitelist[param]
    if not (lo <= new <= hi) or new == old:
        return None
    metric = str(p.get("verdict_metric") or "")
    if metric not in ("net_bp_per_leg", "stop_loss_total", "fills_per_hour", "roundtrip_y"):
        metric = "roundtrip_y" if param in FLOW_SAFE_PARAMS else "net_bp_per_leg"
    return {"action": "adjust", "param": param, "old": old, "new": new,
            "rollback": rollback, "reason": str(p.get("reason") or "")[:300],
            "verdict_metric": metric,
            "verdict_rule": str(p.get("verdict_rule") or "")[:200]}


def apply_proposal(p: Dict[str, Any]) -> bool:
    """护栏通过后的落库。主动流参数只写 flow_learn_params，不改做市注册表。"""
    try:
        if p["param"] in FLOW_SAFE_PARAMS:
            current = load_learn_params(ROOT)
            if abs(float(current.get(p["param"]) or 0.0) - float(p["old"])) > 1e-6:
                return False
            current[p["param"]] = float(p["new"])
            save_learn_params(ROOT, current)
            _remember_pending(p)
            return True
        from backend.services import lane_registry as reg
        from backend.services.market_maker import evolution as evo
        meta = (reg.get_lane(LANE) or {}).get("meta") or {}
        params = dict(meta.get("params") or {})
        param = p["param"]
        if abs(float(params.get(param) or 0.0) - float(p["old"])) > 1e-9:
            return False          # 注册表已被外部改动 ⇒ 建议过期,拒绝
        prev = {param: params.get(param)}
        params[param] = float(p["new"])
        meta["params"] = params
        ok = evo._apply_params(LANE, meta, {param: float(p["new"])}, prev=prev,
                               reason=f"[self-tune h706] {p['reason'][:200]}")
        if ok:
            try:
                pending = json.loads(PENDING.read_text(encoding="utf-8"))
            except Exception:
                pending = []
            pending.append({"param": param, "old": p["old"], "new": p["new"],
                            "rollback": p["rollback"], "applied_at": time.time(),
                            "verdict_metric": p["verdict_metric"],
                            "verdict_rule": p["verdict_rule"], "reason": p["reason"],
                            # [h767 2026-10-03 用户指令"全面提速"] 快判窗口 2h → 30 分钟
                            # (测试阶段:加快"改→评→学"的循环;可用 MM_VERDICT_DELAY_SEC 调)
                            "verdict_at": time.time() + float(
                                os.environ.get("MM_VERDICT_DELAY_SEC", "1800"))})
            PENDING.parent.mkdir(parents=True, exist_ok=True)
            PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2),
                               encoding="utf-8")
            _sync_live()
        return bool(ok)
    except Exception:
        return False


def _sync_live() -> None:
    """实盘车道必须与纸面一致(用户硬要求)。"""
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("sync_live_cfg",
                                             ROOT / "scripts" / "sync_live_cfg.py")
        _m = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_m)
        _m.main()
    except Exception:
        pass


def _remember_pending(p: Dict[str, Any]) -> None:
    import os
    try:
        pending = json.loads(PENDING.read_text(encoding="utf-8"))
    except Exception:
        pending = []
    pending.append({
        "param": p["param"], "old": p["old"], "new": p["new"],
        "rollback": p["rollback"], "applied_at": time.time(),
        "verdict_metric": p["verdict_metric"],
        "verdict_rule": p["verdict_rule"], "reason": p["reason"],
        "era": "flow" if p["param"] in FLOW_SAFE_PARAMS else "mm",
        "verdict_at": time.time() + float(os.environ.get("MM_VERDICT_DELAY_SEC", "1800")),
    })
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")


def write_review_request(ev: Dict[str, Any], raw: Optional[str], why: str) -> None:
    """[h706/h707] 写 DSH 桥请求:**附上演化记忆**(玩法手册 + 最近经验),
    让复核方(DSH,deepseek v41 flash)带着历史积累做决策——越用越聪明。"""
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    active = ev.get("strategy") == "active_flow"
    book = playbook_for_prompt(load_playbook()) if active else load_playbook()
    exp = experience_for_prompt(load_experience(10)) if active else load_experience(10)
    REVIEW.write_text(json.dumps(
        {"ts": time.time(), "why": why, "raw_llm": (raw or "")[:600],
         "evidence": {k: v for k, v in ev.items() if k != "current_params"},
         "playbook": book,
         "experience_recent": exp},
        ensure_ascii=False, indent=2), encoding="utf-8")


def _count_flow_roundtrips() -> int:
    path = ROOT / "data" / "flow_roundtrip_log.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return 0
    n = 0
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("era") == "flow" and row.get("y_bp") is not None:
            n += 1
    return n


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    # [h706c] 默认**直连 DSH 桥**(用户:"你不就是 DSH 么,还用什么 API")。
    # 项目侧不调任何 LLM API;只有显式加 --llm 才用 .env 的 DeepSeek Key 直连
    # (完全自主模式,可选)。
    ap.add_argument("--llm", action="store_true", help="用 DEEPSEEK_API_KEY 直连自调(默认走 DSH 桥)")
    ap.add_argument("--lane", default=LANE)
    a = ap.parse_args()
    ev = collect_evidence(a.hours)
    if ev.get("strategy") == "active_flow":
        n_rt = _count_flow_roundtrips()
        print(f"主动流证据:往返 {n_rt} 趟。做市腿数不作为开门条件。")
        if n_rt < 30:
            ev["instruction"] = "样本不足，只允许 hold"
            write_review_request(ev, None, "active-flow-hold(独立往返<30)")
            print("✗ 独立往返不足 30，桥上只允许保持不动。")
            return 0
        msgs = build_messages(ev)
        if a.llm:
            raw = call_llm(msgs)
            prop = parse_proposal(raw or "", FLOW_SAFE_PARAMS) if raw else None
            if prop and prop.get("action") == "adjust" and a.apply:
                ok = apply_proposal(prop)
                print("✓ 主动流提案已写入" if ok else "✗ 主动流提案被拒绝")
                return 0 if ok else 4
        write_review_request(ev, None, "active-flow-bridge")
        print("✓ 已写主动流 DSH 桥请求。")
        return 0
    legs = (ev.get("window") or {}).get("legs") or 0
    print(f"证据:窗口 {a.hours}h / {legs} 腿 / 净 {ev.get('window', {}).get('net_usd')}U")
    if legs < 50:
        print("✗ 样本不足(n<50),不写请求。")
        return 0
    msgs = build_messages(ev)
    if a.llm:
        raw = call_llm(msgs)
        if raw:
            prop = parse_proposal(raw)
            if prop and prop.get("action") == "adjust" and a.apply:
                ok = apply_proposal(prop)
                print("✓ [--llm] 直连提案已落库" if ok else "✗ [--llm] 落库失败")
                return 0 if ok else 4
            print("[--llm] " + json.dumps(prop or {"raw": raw[:200]},
                                          ensure_ascii=False))
            return 0
        print("[--llm] 直连不可用/无 Key ⇒ 走 DSH 桥")
    # 默认通道:写请求文件,由 DSH(本会话定时提醒)读取、推理、按护栏落地
    write_review_request(ev, None, "dsd-bridge(默认直连 DSH,不走项目 LLM API)")
    print("✓ 已写 logs/self_tuner_review.json —— DSH 桥将在下一次提醒(≤2h)复核。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
