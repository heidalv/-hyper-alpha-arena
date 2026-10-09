# -*- coding: utf-8 -*-
"""[M3 / P1 2026-09-21] LLM 诊断层 —— **只看不改**（action 恒为 keep）。

# 对应设计 v3 的 M3，且执行 P1 阶段的硬约束

  · 只在 **M2 有标记时**调用（无标记不调：省成本 + 避免"为调而调"）
  · 输出严格 JSON，含 diagnosis / cause / confidence / params
  · **P1 阶段 `action` 恒为 `keep`** ⇒ 绝不改任何参数（零风险）
  · 落库 `logs/llm_monitor_log.jsonl`（JSONL，便于后续入库与统计）
  · 机械校验：reason 必须含给定数字，否则该条作废

# 校验规则（可自动判，不靠人看）

  R1 `diagnosis` 与 `reason` **各含至少一个给定统计里的数字** —— 否则作废
      理由：这是防"泛泛而谈"的唯一机械手段。LLM 说"该币风险偏高"没有信息量；
      说"近30min行情项 −5.8bp"才可核对。
  R2 `cause` ∈ {structural, market_event, execution_decay, param_mismatch}
  R3 `confidence ∈ [0,1]`
  R4 `params` 的键 ⊂ 白名单（P1 阶段不应用，仅记录"它想改什么"）

# 为什么 P1 只记录不应用

  要回答"LLM 诊断准不准"，必须先有一批**未被它影响的**窗口作基线。
  若一上线就让它改参数，就再也分不清"变好是因为它诊断对"还是"因为改了参数"。

用法：
    .venv\\Scripts\\python.exe scripts\\m3_llm_diagnose.py              # 当前窗口（有标记才调）
    .venv\\Scripts\\python.exe scripts\\m3_llm_diagnose.py --force       # 强制调用（测试用）
    .venv\\Scripts\\python.exe scripts\\m3_llm_diagnose.py --replay 14:20 # 对某个历史时点诊断
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

import m1m2_monitor as M  # noqa: E402

LOG = ROOT / "logs" / "llm_monitor_log.jsonl"

# 参数白名单（与设计 v3 §4 一致）。**安全参数不在其中**：
# stop_loss_bp / stop_loss_vol_min / compound_ratio / max_*_ratio / daily_loss_stop_pct
PARAM_WHITELIST = {"spread_mult", "spread_mult_reduce", "take_profit_bp",
                   "min_width_bp", "k_inv"}
CAUSES = {"structural", "market_event", "execution_decay", "param_mismatch"}

SYSTEM = """你是量化做市系统的**诊断助手**。你的任务不是预测价格，而是**读给定的指标、
找出异常的原因、并判断是否需要调整参数**。

严格规则：
1. 只输出 JSON，不要任何解释文字、不要代码围栏。
2. `diagnosis` 与 `reason` **必须各包含至少一个给定统计里的具体数字**（如 "行情项 −5.8bp"）。
   若无法引用数字，说明证据不足 ⇒ `confidence` 必须 ≤ 0.2。
3. `cause` 只能是这四个之一：structural / market_event / execution_decay / param_mismatch
   · structural       = 该币结构不适合我们（点差太宽、深度不足）
   · market_event     = 行情单边、事件驱动（行情项持续不利）
   · execution_decay  = 我们的挂单不再被成交（成交率降、被别人抢内盘）
   · param_mismatch   = 参数与当前环境不匹配（例如挂太宽/太窄）
4. `params` 只能建议这些键（不要碰止损/杠杆/敞口上限）：
   spread_mult, spread_mult_reduce, take_profit_bp, min_width_bp, k_inv
5. **允许说"什么都不用改"**（`params` 给空对象）—— 这是合法的、常见的好答案。
6. `confidence` ∈ [0, 1]。

输出格式：
{"symbols":[{"symbol":"XRP","diagnosis":"...","cause":"...","confidence":0.0,
             "params":{},"reason":"..."}],
 "global":{"regime":"trending|ranging|high_vol|event_driven","note":"...","confidence":0.0}}"""


def build_user_prompt(snap: dict, flags_by_sym: dict) -> str:
    lines = ["下面是**当前在营交易对**的客观指标。请诊断。\n"]
    for sym, s in snap["symbols"].items():
        fl = s.get("flags") or []
        lines.append(f"### {sym}   命中标记: {', '.join(fl) if fl else '无'}")
        for w in (10, 30, 60):
            d = s["windows"][w]
            lines.append(
                f"  近{w}min: 笔数={d['fills']} 名义=${d['notional']:,.0f} "
                f"价差={d['spread_bp']:+.3f}bp 行情={d['price_bp']:+.3f}bp "
                f"费={d['fee_bp']:+.3f}bp 净={d['net_bp']:+.3f}bp 净额=${d['net_usd']:+.4f} "
                f"最差单笔=${d['worst_fill_usd']:.4f}")
        b = s["baseline"]
        lines.append(f"  全时代基线: 每笔净={b['net_bp_per_fill']:+.4f}bp "
                     f"强平率={(b['flatten_rate'] or 0)*100:.1f}% "
                     f"p95单笔=${b['p95_abs_fill_usd']:.4f} 总笔数={b['fills']}")
        lines.append(f"  近3×10min 行情bp序列: {[round(x,3) for x in s['series_10m']]}")
        lines.append(f"  结构: p25点差={s['p25_spread_bp']}bp")
    lim = M.live_limits()
    par = M.live_params()
    lines.append("\n### 当前生效参数")
    for k in ("spread_mult", "spread_mult_reduce", "take_profit_bp"):
        lines.append(f"  {k}={par.get(k)}")
    for k in ("stop_loss_bp", "max_net_directional_ratio", "timeout_exit_maker_only",
              "reduce_quote_disabled"):
        lines.append(f"  {k}={lim.get(k)}")
    lines.append("\n请按格式输出 JSON。记住：允许说「什么都不用改」。")
    return "\n".join(lines)


def _nums_in(text: str) -> set:
    """抽文本里的数字（含小数与负号），用于"必须引用给定数字"的校验。"""
    out = set()
    for m in re.finditer(r"-?\d+\.?\d*", str(text or "")):
        try:
            out.add(round(abs(float(m.group(0))), 4))
        except Exception:
            pass
    return out


def numbers_from_prompt(prompt: str) -> set:
    return _nums_in(prompt)


def validate(out: dict, prompt_nums: set) -> tuple:
    """机械校验。返回 (ok, 问题列表)。**任一条不过 ⇒ 该条作废**。"""
    problems = []
    if not isinstance(out, dict):
        return False, ["输出不是 JSON 对象"]
    syms = out.get("symbols")
    if not isinstance(syms, list) or not syms:
        problems.append("缺少 symbols 数组")
    for s in (syms or []):
        sym = s.get("symbol", "?")
        diag = str(s.get("diagnosis") or "")
        reason = str(s.get("reason") or "")
        # R1 必须引用数字
        for lab, txt in (("diagnosis", diag), ("reason", reason)):
            if not (_nums_in(txt) & prompt_nums):
                problems.append(f"{sym}.{lab} 未引用给定数字（R1）")
        # R2 cause 枚举
        if s.get("cause") not in CAUSES:
            problems.append(f"{sym}.cause={s.get('cause')!r} 不在枚举内（R2）")
        # R3 confidence
        c = s.get("confidence")
        if not isinstance(c, (int, float)) or not (0.0 <= float(c) <= 1.0):
            problems.append(f"{sym}.confidence={c!r} 非法（R3）")
        # R4 params 白名单
        p = s.get("params") or {}
        if not isinstance(p, dict):
            problems.append(f"{sym}.params 不是对象（R4）")
        else:
            bad = [k for k in p if k not in PARAM_WHITELIST]
            if bad:
                problems.append(f"{sym}.params 含非白名单键 {bad}（R4）")
    return (len(problems) == 0), problems


def call_llm_safe(prompt_user: str, timeout_s: float = 120.0):
    """调用现成的 LLM 通路；任何失败返回 (None, 原因)。"""
    try:
        from backend.services.factors_lab.common import call_llm, parse_json_block
    except Exception as e:
        return None, f"import 失败: {type(e).__name__}: {e}"
    t0 = time.time()
    raw = call_llm(SYSTEM, prompt_user, caller="mm_monitor_m3",
                   max_tokens=1200, temperature=0.2)
    dt = time.time() - t0
    if not raw:
        return None, f"调用失败/无配置（{dt:.1f}s）"
    obj = parse_json_block(raw)
    if obj is None:
        return None, f"JSON 解析失败（{dt:.1f}s）：{raw[:200]}"
    return {"obj": obj, "raw": raw, "latency_ms": int(dt * 1000)}, None


def guard_suggestions(out: dict) -> list:
    """对 LLM 的每条 `params` 建议跑 **G1 实测最优守卫**，返回裁决列表。

    P1 阶段不应用任何改动，但**裁决必须现在就跑**：
      · 它是"LLM 想改的东西能不能改"的唯一机械防线
      · 提前跑能积累"它多久会提出一次越界建议"的统计，决定 P2 是否敢开放
    """
    try:
        from g1_measured_optimum import check_suggestion
    except Exception as e:
        return [{"error": f"守卫 import 失败: {e}"}]
    out_list = []
    for s in (out.get("symbols") or []):
        for k, v in (s.get("params") or {}).items():
            allowed, reason = check_suggestion(k, v)
            out_list.append({"symbol": s.get("symbol"), "key": k, "value": v,
                             "allowed": bool(allowed), "reason": reason})
    return out_list


def append_log(rec: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def apply_guarded(verdicts: list, rec: dict, *, now: datetime,
                  timeout_s: float = 150.0) -> dict:
    """**P2**：把过了 G1 的建议送进 G2 限额 → G3 写入并核对 → 失败自动回滚。

    ══ 为什么默认不调用本函数 ══
    P1 的全部价值在于"先证明只看不改也能跑对"。本函数只在 `--apply` 下进入，
    默认路径逐字不变 ⇒ 回退只需去掉命令行开关（零代码改动）。

    ══ 四道闸，顺序不可换 ══

      ① **G1 实测最优守卫**：已在 `guard_suggestions()` 里跑完。
         只有 `allowed=True` 的才进下一步 —— 越界建议**连写都不写**。

      ② **作用域闸（本函数新增）**：LLM 的输出是**按币**的
         （`symbols[].params`），但注册表的 `params` 是**全车道一份**。
         多个币对同一个键给出不同值时**无法同时满足** ⇒ 整键拒绝，
         而不是"谁先来谁赢"（那会变成一个随字典顺序变化的隐蔽 bug）。
         同键同值 ⇒ 不算冲突，正常通过。

      ③ **[F327] 需重启闸**：`needs_restart(key)` 为真 ⇒ 本键**不可**靠写注册表生效
         （`MM_REGISTRY_AUTHORITATIVE=1` 已消掉这一情形，但守卫不能依赖配置）。
         拒绝而不是"写了但没生效" —— 后者正是 F189/F280/F287/F292 那条
         **静默偏差**老路：注册表显示新值、实盘跑旧值，后续所有归因都建在错前提上。

      ④ **G2 限额**：单次 ≤1 币、单币 ≤2 次/小时、全局 ≥10 分钟间隔。
         被 G2 拒的条目**不占配额**，也不写注册表。

    写入走 G3 的 `apply_and_verify`：写注册表 → 读**运行态心跳**核对 →
    `timeout_s` 内没生效就**回滚**。这一条是防"写了但没生效"的最后一道。

    ══ 为什么核对的超时给 150s ══
    F251 热采用的指纹检查周期是 **60s**（`_maybe_reload_meta`），
    再叠加心跳写入间隔 ~15s ⇒ 150s 才有足够余量，否则会把"还没轮到"
    误判成"没生效"并误回滚。
    """
    from g1_measured_optimum import check_suggestion
    from g2_rate_limit import append_adjustment, check_call
    from g3_apply_verify import apply_and_verify, needs_restart

    out = {"attempted": [], "conflicts": [], "restart_bound": [],
           "rate_rejected": [], "applied": [], "verify_failed": []}

    # ① 汇总"已过 G1"的建议（每个键记下所有提出者与值）
    per_key: dict = {}
    for v in verdicts:
        if v.get("error") or not v.get("allowed"):
            continue
        key, val, sym = v.get("key"), v.get("value"), v.get("symbol")
        if key is None:
            continue
        per_key.setdefault(key, []).append((sym, val))

    # ② 作用域闸：同键多值 ⇒ 冲突
    items = []
    for key, pairs in sorted(per_key.items()):
        vals = {p[1] for p in pairs}
        if len(vals) > 1:
            out["conflicts"].append({
                "key": key, "rule": "scope",
                "reason": f"多个币对同一键给出不同值 {sorted(pairs, key=str)}；"
                          f"注册表参数是全车道一份 ⇒ 无法同时满足 ⇒ 整键拒绝"})
            continue
        sym, val = pairs[0]
        # ③ 需重启闸
        if needs_restart(key):
            out["restart_bound"].append({
                "key": key, "value": val, "symbol": sym,
                "reason": f"{key} 在 env 白名单里 ⇒ 写注册表不生效（须重启）⇒ 拒绝"})
            continue
        items.append({"symbol": sym, "key": key, "value": val})

    if not items:
        rec["action"] = "keep"
        rec["applied"] = False
        rec["apply_detail"] = out
        return rec

    # ④ G2 限额
    allowed, rejected = check_call(items, now=now)
    out["rate_rejected"] = rejected
    for r in rejected:
        append_adjustment(symbol=r.get("symbol", ""), key=r.get("key", ""),
                          value=r.get("value"), applied=False,
                          rule=r.get("rule", ""), reason=r.get("reason", ""),
                          now=now)

    if not allowed:
        rec["action"] = "keep"
        rec["applied"] = False
        rec["apply_detail"] = out
        return rec

    # G3：写入 + 核对 + 自动回滚
    it = allowed[0]
    res = apply_and_verify(it["key"], it["value"], timeout_s=timeout_s)
    out["attempted"].append(res)
    append_adjustment(symbol=it["symbol"], key=it["key"], value=it["value"],
                      applied=bool(res.get("verified")),
                      rule="G3",
                      reason=res.get("note", ""),
                      old_value=res.get("old"), now=now)

    if res.get("verified"):
        out["applied"].append(res)
        rec["action"] = "adjust"
        rec["applied"] = True
    else:
        out["verify_failed"].append(res)
        rec["action"] = "keep"          # 没证明生效 ⇒ **不算调整**
        rec["applied"] = False

    rec["apply_detail"] = out
    return rec


def run_once(t_end: datetime, symbols: list, *, force: bool = False,
             do_log: bool = True, do_apply: bool = False) -> dict:
    snap = M.build_snapshot(t_end, symbols)
    flagged = {s: v["flags"] for s, v in snap["symbols"].items() if v["flags"]}
    print(f"\n  as_of {t_end.isoformat()[:19]}")
    for s, v in snap["symbols"].items():
        print(f"    {s:<8} 标记 {v['flags'] or '（无）'}   近30min 净 "
              f"{v['windows'][30]['net_bp']:+.3f}bp  行情 "
              f"{v['windows'][30]['price_bp']:+.3f}bp")
    if not flagged and not force:
        print(f"\n  ⇒ 无异常标记 ⇒ **不调 LLM**（按设计）")
        return {"skipped": True, "reason": "no_flags"}
    if force and not flagged:
        print(f"\n  （--force：无标记也调用，仅用于测试）")

    prompt = build_user_prompt(snap, flagged)
    pnums = numbers_from_prompt(prompt)
    print(f"  prompt {len(prompt)} 字符，含 {len(pnums)} 个不同数字")
    res, err = call_llm_safe(prompt)
    rec = {"ts": datetime.now().astimezone().isoformat(),
           "as_of": t_end.isoformat(), "flags": flagged,
           "symbols": list(snap["symbols"]), "prompt_len": len(prompt),
           "action": "keep", "applied": False}
    if err:
        rec.update({"parse_ok": False, "error": err, "fallback": True})
        if do_log:
            append_log(rec)
        print(f"\n  ✗ {err}")
        print(f"  ⇒ fail-open：退回纯机械层（宇宙与参数均不变）")
        return rec

    out = res["obj"]
    ok, problems = validate(out, pnums)
    verdicts = guard_suggestions(out)
    rec.update({"parse_ok": True, "rules_ok": ok, "problems": problems,
                "latency_ms": res["latency_ms"], "llm_out": out,
                "guard_verdicts": verdicts,
                "guard_any_rejected": any(not v.get("allowed") for v in verdicts)})
    if do_log:
        append_log(rec)

    print(f"\n  ── LLM 诊断（{res['latency_ms']}ms）──")
    for s in out.get("symbols") or []:
        print(f"    {s.get('symbol')}: cause={s.get('cause')} "
              f"conf={s.get('confidence')}")
        print(f"      诊断: {s.get('diagnosis')}")
        print(f"      理由: {s.get('reason')}")
        if s.get("params"):
            print(f"      **它想改**: {s.get('params')}")
    g = out.get("global") or {}
    if g:
        print(f"    全局: regime={g.get('regime')} conf={g.get('confidence')} "
              f"note={g.get('note')}")
    print(f"\n  ── 机械校验 ──")
    print(f"    规则校验：{'**通过**' if ok else '**不通过**'}")
    for p in problems:
        print(f"      ✗ {p}")

    print(f"\n  ── G1 实测最优守卫裁决 ──")
    if not verdicts:
        print(f"    （本次没有参数建议）")
    for v in verdicts:
        if v.get("error"):
            print(f"    ✗ {v['error']}")
            continue
        mark = "放行" if v.get("allowed") else "**拒绝**"
        print(f"    {v.get('symbol')}: {v.get('key')}={v.get('value')} -> {mark}")
        print(f"      {v.get('reason')}")
    n_rej = sum(1 for v in verdicts if not v.get("allowed"))
    if verdicts:
        print(f"    ⇒ {n_rej}/{len(verdicts)} 条被守卫拒绝")

    # ── P2：应用链（默认关闭）──────────────────────────────────────────
    if not do_apply:
        rec["action"] = "keep"
        rec["applied"] = False
        print(f"    ⇒ 未加 --apply ⇒ action 恒为 **keep**（不应用任何参数改动）")
        return rec

    # 规则校验不过 ⇒ **不应用**。理由：校验不过说明输出本身不合契约，
    # 此时它的 params 同样不可信（F328 的教训：宁可少做，不可带着错前提做）。
    if not ok:
        rec["action"] = "keep"
        rec["applied"] = False
        rec["apply_detail"] = {"skipped": "rules_failed", "problems": problems}
        print(f"    ⇒ 规则校验不通过 ⇒ **不应用**（宁可少做，不可带错前提做）")
        return rec

    rec = apply_guarded(verdicts, rec, now=datetime.now().astimezone())
    d = rec.get("apply_detail") or {}
    print(f"\n  ── P2 应用链 ──")
    print(f"    冲突拒绝 {len(d.get('conflicts') or [])}  "
          f"需重启拒绝 {len(d.get('restart_bound') or [])}  "
          f"限额拒绝 {len(d.get('rate_rejected') or [])}")
    for c in (d.get("conflicts") or []) + (d.get("restart_bound") or []):
        print(f"      ✗ {c.get('key')}={c.get('value')}: {c.get('reason')}")
    for r in (d.get("rate_rejected") or []):
        print(f"      ✗ [{r.get('rule')}] {r.get('key')}={r.get('value')}: "
              f"{r.get('reason')}")
    for a in (d.get("attempted") or []):
        mark = "已生效" if a.get("verified") else "**未生效**"
        roll = "（已回滚）" if a.get("rolled_back") else ""
        print(f"      {a.get('key')}: {a.get('old')} -> {a.get('new')}  {mark}{roll}")
        print(f"        {a.get('note')}")
    print(f"    ⇒ action = **{rec.get('action')}**  applied = {rec.get('applied')}")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="无标记也调用（测试）")
    ap.add_argument("--replay", default="", help="对历史时点诊断，如 14:20")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="P2：把过了 G1/G2/G3 的建议**真的写进注册表**"
                         "（默认关闭 ⇒ 只看不改）")
    a = ap.parse_args()

    syms = ([x.strip().upper() for x in a.symbols.split(",") if x.strip()]
            or ["ASTER", "XRP", "SOL"])
    print("=" * 92)
    print("M3  LLM 诊断层（" + ("P2：守卫链放行后应用" if a.apply else "P1：只看不改") + "）")
    print("=" * 92)
    if a.replay:
        hh, mm = a.replay.split(":")
        t = datetime(2026, 9, 21, int(hh), int(mm)).astimezone()
    else:
        t = datetime.now().astimezone()
    rec = run_once(t, syms, force=a.force, do_log=not a.no_log,
                   do_apply=a.apply)
    print(f"\n  日志：{LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
