# -*- coding: utf-8 -*-
"""[h650 2026-09-30] mm 车道离线研究员(LLM 新角色,零线上权限)。

《方向判定_因子还是LLM_判定与设计_20260930》§5:
  · 身份 = 离线研究员,不是交易员;只提"候选闸门假设",不做执行结论;
  · 输入 = 代码算好的数字(心跳计数 / quote-time 归因腿 / 价差),LLM 不负责算术;
  · 输出 = JSON 提案 ≤3 条,落研究队列文件,零权限(不落地 = 提案无效);
  · 频率 ≤ 6 次/天,temperature=0。
已证定律与已排除路径写死在 system prompt 里,防止推翻/重复发明。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[3]

QUEUE_FILE = ROOT / "data" / "mm_researcher_queue.jsonl"

# [h650] 已证定律与已排除路径(研究侧结论,研究员不得推翻/重复提案)
KNOWN_LAWS: List[str] = [
    "统一流定律(h351/h355/h358/h361):30s-5min 内任何形态的期望方向由「当前 OFI 流向与交易方向是否同向」决定;with 全正(t=10~22),against 全负(t=-7~-18)。不 fade 流 = 唯一硬规律。",
    "趋势对齐(h324):强趋势中顺势加仓 +3.75bp/腿 vs 逆势 −4.18bp/腿;趋势闸只封逆势加仓侧,减仓侧豁免。",
    "形态×币匹配(h367):反转族(P1回调/P2 VWAP回归)限 vr60<~1.3 的币;动量族(P4/P5)不限;BNB(vr60=1.39)只用动量族。",
    "成交时刻归因被被动成交机制机械污染(h369):只可用 quote-time(挂单时刻)归因。",
    "已排除路径:OFI 预测逆选择(负)、成交规模信号预测逆选择(负)、h284 填充模型对被动形态系统性悲观(不可作影子测验工具)。",
    "挂单距离:贴盘口优于加宽(h364);加宽报价 = 双输。",
    "方向差在币×边,不在全局多空(方向判定_20260930):ASTER 卖 −4.43bp / UNI 卖 +6.36bp。",
]

SYSTEM_PROMPT = (
    "你是 Hyper-Alpha-Arena 高频做市模块(mm_asterdex 车道)的离线研究员。\n"
    "你的唯一职责:提出可验证的方向闸候选假设。你不是交易员,不对任何线上参数做决定。\n\n"
    "已证定律(禁止推翻或重复发明,提案不得与之矛盾):\n"
    + "\n".join(f"{i+1}. {x}" for i, x in enumerate(KNOWN_LAWS))
    + "\n\n硬边界:\n"
    "- 禁止输出「应该开/关某闸」的执行结论,只提「候选假设 + 预期 bp/腿 + 最小样本 + 判决标准」。\n"
    "- 禁止提「全车道只做多/只做空」;禁止把一个币两边都停掉;禁止停减仓侧。\n"
    "- 样本不足(n<15/边)时必须输出空提案,不许编。\n"
    "- 每条 rationale 必须引用输入块的编号 {S1..S4},禁止引用编号外的数据。\n\n"
    "只输出一个 JSON 对象,不要其他文字。"
)


def build_user_prompt(stats: Mapping[str, Any]) -> str:
    """把代码算好的数字组装成 {S1..S4} 输入块。"""
    def _j(x: Any) -> str:
        return json.dumps(x, ensure_ascii=False, default=str)

    s1 = stats.get("gate_stats") or {}
    s2 = stats.get("leg_stats") or {}
    s3 = stats.get("symbol_stats") or {}
    s4 = stats.get("excluded") or []
    return (
        f"{{S1}} 各闸触发计数(近 4 小时,心跳口径):{_j(s1)}\n"
        f"{{S2}} 逐币×逐边 quote-time 归因(近 4 小时:笔数/价格方向bp):{_j(s2)}\n"
        f"{{S3}} 逐币行情签名(最新市场价差 bp):{_j(s3)}\n"
        f"{{S4}} 已排除路径与防重复清单:{_j(s4)}\n\n"
        '输出格式:\n{"proposals": [\n'
        '  {"gate": "<候选闸名>", "symbol": "<币或 ALL>", "side": "<buy/sell/ALL>",\n'
        '   "threshold": <数值>, "rationale": "<引用 {S1..S4} 编号>",\n'
        '   "expected_bp_per_leg": <数值>, "min_sample_n": <>=15>,\n'
        '   "verdict_criteria": "<预注册判决口径>"}\n'
        "]}\n"
        "没有站得住的假设就输出 {\"proposals\": []}。"
    )


def validate_proposals(data: Any, max_n: int = 3) -> List[Dict[str, Any]]:
    """解析模型输出并校验 schema;坏条目直接丢弃(宁缺勿滥)。"""
    out: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return out
    raw = data.get("proposals")
    if not isinstance(raw, list):
        return out
    for p in raw[:max_n]:
        if not isinstance(p, dict):
            continue
        gate = str(p.get("gate") or "").strip()
        if not gate:
            continue
        symbol = str(p.get("symbol") or "ALL").strip().upper() or "ALL"
        side = str(p.get("side") or "").strip().lower()
        if side not in ("buy", "sell", "all"):
            continue
        thr = p.get("threshold")
        if not isinstance(thr, (int, float)):
            continue
        try:
            expected = float(p.get("expected_bp_per_leg"))
            min_n = int(p.get("min_sample_n") or 15)
        except (TypeError, ValueError):
            continue
        if min_n < 15:
            continue
        rationale = str(p.get("rationale") or "").strip()
        if not rationale:
            continue
        out.append({
            "gate": gate, "symbol": symbol, "side": side,
            "threshold": float(thr), "rationale": rationale,
            "expected_bp_per_leg": expected, "min_sample_n": min_n,
            "verdict_criteria": str(p.get("verdict_criteria") or "").strip(),
        })
    return out


def append_queue(entry: Mapping[str, Any]) -> bool:
    """提案落研究队列(零权限:不落地=提案无效)。失败返回 False 不抛。"""
    try:
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(entry), ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:
        return False


def ask_researcher(stats: Mapping[str, Any], *, max_tokens: int = 800) -> Dict[str, Any]:
    """调用 deepseek-flash(温度 0)产出提案;失败返回 {ok:False, error}。"""
    try:
        from backend.services.llm_config_service import LLMConfig, call_llm_api_sync

        key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not key:
            return {"ok": False, "error": "没有 DEEPSEEK_API_KEY"}
        cfg = LLMConfig(
            id=0, name="mm-researcher", provider="deepseek", model="deepseek-flash",
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
            or "https://api.deepseek.com",
            api_key=key,
        )
        resp = call_llm_api_sync(
            cfg,
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": build_user_prompt(stats)}],
            temperature=0.0, max_tokens=int(max_tokens),
            response_format={"type": "json_object"}, timeout=60,
            caller="mm.researcher", bypass_cache=True,
        )
        if not resp:
            return {"ok": False, "error": "deepseek 空响应"}
        content = resp["choices"][0]["message"]["content"]
        data = json.loads(content or "{}")
        proposals = validate_proposals(data)
        return {"ok": True, "proposals": proposals,
                "model": str(resp.get("model") or "deepseek-flash")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def run_once(lane_id: str = "mm_asterdex", hours: float = 4.0) -> Dict[str, Any]:
    """一次研究员轮次:采集 → 问模型 → 落队列。返回审计摘要(可直接记日志)。"""
    from backend.services.market_maker.direction_card import (
        direction_rows_from_legs, fetch_open_legs,
    )
    from backend.services.market_maker.gather import gather_researcher_stats

    stats = gather_researcher_stats(lane_id=lane_id, hours=hours)
    result = ask_researcher(stats)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "lane_id": lane_id, "hours": hours,
        "ok": bool(result.get("ok")),
        "error": result.get("error", ""),
        "proposals": result.get("proposals", []),
    }
    wrote = append_queue(entry)
    entry["queued"] = wrote
    return entry
