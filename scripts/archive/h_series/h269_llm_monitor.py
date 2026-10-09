# -*- coding: utf-8 -*-
"""H269 P5 LLM 监控：每 10 分钟对交易对评估打分（只记录，不自动改参）。

# 用户原始需求

「不是 ai 选币，是监控和量化分析在交易的这些交易对，必要时可以调整参数，10 分钟一次」

# 设计（全面升级 v2 的 2.7）

1. 收集逐币指标 + 全局状态
2. 用 call_llm 让 LLM 评估打分 + 给参数建议
3. parse_json_block 解析
4. **只记录，不自动改参数**（LLM 建议是参考，改动走人/验证）

# LLM 输入必须包含本轮学到的机制（否则它会在旧框架打转）

  · 框架已从"做市"改成"方向性反转（counter_trend）"
  · 逆势优于顺势（120s lookback，差 +2.5~+4bp）
  · 反转时平仓 +5.30bp vs 固定持有 −1.10bp
  · 持有期 60~120s、止盈 5bp、止损 6bp（已改）

# 用法

    python scripts/h269_llm_monitor.py                 # 单次
    python scripts/h269_llm_monitor.py --loop --interval 600   # 每 10 分钟
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h269_llm_monitor.jsonl"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def collect() -> dict:
    import psycopg
    # [h269c] worker 写状态文件非原子：读到半截 JSON 就重试一次，再失败抛给调用方
    raw = None
    for _ in range(3):
        try:
            raw = (ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8")
            j = json.loads(raw)
            break
        except json.JSONDecodeError:
            time.sleep(2.0)
    if raw is None or j is None:
        raise RuntimeError("mm_lane_status.json 连续读取失败")
    par = dict(j.get("params") or {})
    for k, v in dict(j.get("limits") or {}).items():
        par.setdefault(k, v)
    # 最近 30 分钟逐币 maker 腿
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol,
                       count(*),
                       round(coalesce(sum(net_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(spread_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(price_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(net_bp*notional/1e4),0)::numeric,3)
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - interval '30 minutes'
                  AND (meta_json->'flatten')::text='false'
                GROUP BY 1 ORDER BY 2 DESC
            """, (LANE,))
            syms = [{"symbol": r[0], "n": int(r[1]), "net_bp": float(r[2]),
                     "spread_bp": float(r[3]), "price_bp": float(r[4]),
                     "net_usd": float(r[5])} for r in cur.fetchall()]
    states = {}
    for s, d in (j.get("states") or {}).items():
        states[s] = {"qty": float(d.get("qty") or 0),
                     "quote_bid": bool(d.get("quote_bid")),
                     "quote_ask": bool(d.get("quote_ask"))}
    sk = j.get("skip_counts") or {}
    # [h269b] 最近 60 分钟出口腿质量（SL / decay / TP / orphan 分开）——规则建议的数据源
    exits = {}
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'maker_entry'),
                       count(*),
                       round(coalesce(sum(net_bp*notional)/NULLIF(sum(notional),0),0)::numeric,3)
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - interval '60 minutes'
                GROUP BY 1
            """, (LANE,))
            for r in cur.fetchall():
                exits[r[0]] = {"n": int(r[1]), "wnet_bp": float(r[2])}
    return {
        "at": dt.datetime.now().astimezone().isoformat(),
        "equity": float(j.get("equity") or 0),
        "day_pnl": j.get("day_pnl_usd"), "day_limit": j.get("day_pnl_limit_usd"),
        "ok": j.get("ok"), "ticks": j.get("ticks"), "fills": j.get("fills"),
        "symbols": syms, "states": states,
        "skip_counts": sk,
        "lane_pause": j.get("lane_pause_counts") or {},
        "exit_legs_60m": exits,
        "params": {k: par.get(k) for k in (
            "side_mode", "trend_skew_lookback", "side_trend_min_bp",
            "max_one_side_seconds", "take_profit_bp", "stop_loss_bp",
            "reversal_decay_bp", "vol_pause_sigma", "spread_mult", "spread_mult_reduce",
            "max_leg_notional_mult", "daily_loss_stop_pct", "sudden_move_bp")},
    }


SYSTEM = (
    "你是量化交易系统的监控分析员。这个系统是 asterdex 永续的**被动做市/方向车道**"
    "（300U 账户，maker 0 费 / taker 4bp）。\n\n"
    "2026-09-26/27 根基研究（h330-h373）已用本地数据验证的核心事实（必须据此分析）：\n"
    "1. 该市场是**纯动量结构**（各时域方差比>1），且存在**统一流定律**：任何 30s-5min "
    "形态（P1 薄流回调/P2 薄流VWAP回归/P3 薄流尖峰/P4 双触突破/P5 挤压突破）的期望方向"
    "都由「当前 15s OFI 是否与交易方向同向」决定——with 全正（t 10~22）、against 全负"
    "（t −7~−18），n≈60 万事件零例外。不要再建议「做反转/逆流」类改动。\n"
    "2. 现行线上配置（以脚本读取的实时参数为准）：side_mode=model（h300 工件），"
    "trend_pause_bp=15（封逆势侧），vpin_pause_threshold=0（VPIN 门已关，勿再引用 0.60），"
    "vwap_revert_bp=2.0（P2 无条件形态试跑 #1 进行中），出口 tp30/stop40/timeout120。\n"
    "3. 试跑治理（预注册，不可绕过）：#1 P2 无条件（12:38 判决）→ #2 宇宙扩容 10 币"
    "（13:00）→ #3 P1 薄流闸 → #4 P2 v2 → #5 移 15 分钟模型门 → #6 P5 闸 → #7 P4 闸。"
    "所有参数变更必须走单变量 12h 试跑 + Welch 判定 + 自动回滚；**不要建议绕过队列的"
    "临时调参**，把发现记录为候选即可。\n"
    "4. 出口：反转衰减 3bp/30s 窗、止盈 30bp、超时 120s（maker-only 出口零费）、"
    "止损 40bp 兜底；单腿名义 ≤$900。挂单越贴越好（h364：0.25bp 双优）。\n"
    "5. 宇宙 [BNB, ETH, BTC]（试跑 #2 将扩至 10 币；选币依据=事件研究分形态分榜）。\n"
    "6. 已知结论（勿重复建议）：BNB 是最强动量币（vr60 1.39）→ 反转族形态在 BNB 失效、"
    "动量族仍有效；ETH 是反转族基准币；15 分钟级信号（EWA/r60 模型/r900）出域。\n\n"
    "你的任务：对每个在交易的币打分（0-100），判断是否继续适合当前框架，并给出"
    "**具体的观察建议**（可以是试跑候选，不是绕过治理的临时改参）。输出 JSON：\n"
    '{"symbols":[{"symbol":"X","score":80,"verdict":"keep|replace|watch",'
    '"reason":"一句具体原因","param_advice":"具体建议"}],'
    '"global":{"summary":"一句全局判断","top_action":"一个最该做的动作"}}'
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=float, default=600.0)
    a = ap.parse_args()

    def once():
        from backend.services.factors_lab.common import call_llm, parse_json_block
        data = collect()
        user = json.dumps(data, ensure_ascii=False, indent=2)
        print("=" * 96)
        print(f"H269  LLM 监控  {data['at'][:19]}")
        print("=" * 96)
        print(f"  equity ${data['equity']:.2f}　day_pnl {data['day_pnl']} / "
              f"{data['day_limit']}　ok={data['ok']}")
        print(f"  逐币（30min）：")
        for s in data["symbols"]:
            print(f"    {s['symbol']:<10} n={s['n']:>4} net={s['net_bp']:+.4f}bp "
                  f"spread={s['spread_bp']:+.4f} price={s['price_bp']:+.4f} "
                  f"${s['net_usd']:+.3f}")
        raw = call_llm(SYSTEM, user, caller="h269_llm_monitor", max_tokens=2500)
        if raw is None:
            print("\n  ✗ LLM 无配置或调用失败（call_llm 返回 None）⇒ 降级：只记录指标")
            OUT.parent.mkdir(parents=True, exist_ok=True)
            with OUT.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"data": data, "llm": None}, ensure_ascii=False) + "\n")
            return
        parsed = parse_json_block(raw)
        print("\n  LLM 输出：")
        if isinstance(parsed, dict):
            for s in parsed.get("symbols", []):
                print(f"    {s.get('symbol')}: score={s.get('score')} "
                      f"[{s.get('verdict')}] {s.get('reason')}")
                if s.get("param_advice"):
                    print(f"       建议: {s.get('param_advice')}")
            g = parsed.get("global", {})
            if g:
                print(f"  全局: {g.get('summary')}")
                if g.get("top_action"):
                    print(f"  最该做: {g.get('top_action')}")
        else:
            print(f"  （原始）{str(raw)[:800]}")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"data": data, "llm": parsed}, ensure_ascii=False) + "\n")
        print(f"\n  已记录 {OUT}（只记录，不自动改参）")

        # [h269b 2026-09-23] 规则化建议（结构化、可执行，供学习进化环消费）
        sug = []
        ex = data.get("exit_legs_60m") or {}
        for path, key in (("stop_loss_taker", "stop"), ("reversal_decay_taker", "decay")):
            e = ex.get(path)
            if e and e["n"] >= 5 and e["wnet_bp"] < -15:
                sug.append({"type": "exit_too_deep", "path": path, "n": e["n"],
                            "wnet_bp": e["wnet_bp"],
                            "advice": f"{path} 60min {e['n']} 腿加权 {e['wnet_bp']}bp"
                                      f"（<-15bp）⇒ 检查衰减阈值/宽限时长，勿回固定硬止损"})
        sl = ex.get("stop_loss_taker")
        # [h387 2026-09-27] 阈值 10→6：16:00-17:00 实测 9 笔止损 −520bp 把整段盈利吐回，
        # ≥10 会漏报这类级联（1 笔 ≈ 抹平 44 笔普通腿）。
        if sl and sl["n"] >= 6:
            sug.append({"type": "hard_stop_overactive", "n": sl["n"],
                        "advice": "固定止损腿 ≥6/60min：核查是否波动段集中回吐；"
                                  "候选 #17 保本/移动止损（h375），勿临时放宽 stop"})
        vol_sk = data["skip_counts"].get("vol_pause", 0)
        ticks = max(int(data.get("ticks") or 1), 1)
        if vol_sk / ticks > 0.3:
            sug.append({"type": "vol_pause_high", "ratio": round(vol_sk / ticks, 3),
                        "advice": "vol_pause 占比 >30%：考虑 vol_pause_sigma 上调（低波动反转更强）"})
        for s in data["symbols"]:
            if s["n"] >= 15 and s["net_bp"] < -1.0:
                sug.append({"type": "symbol_bleeding", "symbol": s["symbol"], "n": s["n"],
                            "net_bp": s["net_bp"],
                            "advice": f"{s['symbol']} 30min {s['n']} 腿 −{abs(s['net_bp'])}bp："
                                      "从候选池（BNB/DOGE/SUI/LTC）评估替换"})
        SUG_OUT = ROOT / "research_l1" / "out" / "h269b_suggestions.jsonl"
        with SUG_OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": data["at"], "suggestions": sug,
                                "llm_top": (parsed or {}).get("global", {}) if isinstance(parsed, dict) else None},
                               ensure_ascii=False) + "\n")
        if sug:
            print(f"\n  [规则建议 {len(sug)} 条] → {SUG_OUT.name}")
            for s in sug:
                print(f"    · {s['type']}: {s['advice'][:80]}")
        else:
            print("\n  [规则建议] 无（出口/波动/逐币指标均在正常带内）")

    if a.loop:
        while True:
            try:
                once()
            except Exception as e:  # 单轮失败不退出 loop（状态文件竞态等）
                print(f"  ✗ 本轮异常（跳过，继续 loop）: {e}")
            time.sleep(a.interval)
    else:
        once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
