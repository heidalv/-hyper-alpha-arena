# -*- coding: utf-8 -*-
"""[h626] 方向卡：用最近开仓腿判定「这个币哪一边先别加仓」。

每 5 分钟重算一次。依据窗口是最近 60 分钟，否则 5 分钟内凑不满样本。
只停更差的那一边。样本不足、两边差不多、或两边都差但分不开，就两边都留。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional


def judge_direction(
    rows: Iterable[Mapping[str, object]],
    *,
    min_n: int = 15,
    worse_bp: float = -2.0,
    gap_bp: float = 2.0,
    min_n_by: Optional[Mapping[str, int]] = None,
) -> Dict[str, str]:
    """返回 `{币: 要停的加仓边}`，边是 `buy` 或 `sell`。每个币最多停一边。

    每一行：`symbol`、`side`（buy/sell）、`n`、`price_bp`（名义加权）。
    [h652 §9.3 候选] `min_n_by` = 逐币最小样本(自适配,未启用时 None ⇒ 全局 min_n)。
    """
    def _min_for(sym: str) -> int:
        if min_n_by and str(sym).upper() in min_n_by:
            return int(min_n_by[str(sym).upper()])
        return int(min_n)

    by: Dict[str, Dict[str, Mapping[str, object]]] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        side = str(row.get("side") or "").lower()
        if side in ("long", "b"):
            side = "buy"
        elif side in ("short", "s"):
            side = "sell"
        if not sym or side not in ("buy", "sell"):
            continue
        by.setdefault(sym, {})[side] = row

    card: Dict[str, str] = {}
    for sym, sides in by.items():
        buy = sides.get("buy")
        sell = sides.get("sell")
        if not buy or not sell:
            continue
        worse: Optional[str] = None
        worst_bp = 0.0
        for side, other in (("buy", sell), ("sell", buy)):
            n = int(sides[side].get("n") or 0)
            px = float(sides[side].get("price_bp") or 0.0)
            other_px = float(other.get("price_bp") or 0.0)
            if n < _min_for(sym):
                continue
            if px > float(worse_bp):
                continue
            if px > other_px - float(gap_bp):
                continue
            if worse is None or px < worst_bp:
                worse = side
                worst_bp = px
        if worse:
            card[sym] = worse
    return card


def fetch_open_legs(*, lane_id: str, minutes: float = 60.0) -> List[Dict[str, object]]:
    """[h651] 已迁移至 attribution.fetch_open_legs(共享归因,§9.1)。

    兼容再导出:方向卡/研究员/判决脚本同一份实现与口径。
    """
    from backend.services.market_maker.attribution import (
        fetch_open_legs as _impl,
    )
    return _impl(lane_id=lane_id, minutes=minutes)


def direction_rows_from_legs(
    legs: Iterable[Mapping[str, object]],
    mids: Mapping[str, float],
) -> List[Dict[str, object]]:
    """[h651] 已迁移至 attribution.direction_rows_from_legs(共享归因,§9.1)。"""
    from backend.services.market_maker.attribution import (
        direction_rows_from_legs as _impl,
    )
    return _impl(legs, mids)



def summarize_direction(rows: Iterable[Mapping[str, object]], card: Mapping[str, str]) -> str:
    """一行中文摘要，给面板滚屏。没有样本也要有一句，不能留白。"""
    by: Dict[str, Dict[str, Mapping[str, object]]] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        side = str(row.get("side") or "").lower()
        if side in ("long", "b"):
            side = "buy"
        elif side in ("short", "s"):
            side = "sell"
        if not sym or side not in ("buy", "sell"):
            continue
        by.setdefault(sym, {})[side] = row
    if not by:
        return "最近60分钟没有开仓样本，两边都挂。"

    def one(label: str, row: Optional[Mapping[str, object]]) -> str:
        if not row:
            return f"{label}无"
        n = int(row.get("n") or 0)
        px = float(row.get("price_bp") or 0.0)
        return f"{label}{n}笔 {px:+.1f}bp"

    parts = []
    for sym in sorted(by):
        buy = by[sym].get("buy")
        sell = by[sym].get("sell")
        nb = int((buy or {}).get("n") or 0)
        ns = int((sell or {}).get("n") or 0)
        blocked = card.get(sym)
        if blocked == "sell":
            tail = "停卖出加仓"
        elif blocked == "buy":
            tail = "停买入加仓"
        elif nb < 15 or ns < 15:
            tail = "样本不够，两边都挂"
        else:
            tail = "分不开，两边都挂"
        parts.append(f"{sym} {one('买', buy)} / {one('卖', sell)} → {tail}")
    return "；".join(parts) + "。"


# DeepSeek V4 Flash 的现行接口名。旧名 deepseek-v4-flash 只是临时别名。
FLASH_MODEL = "deepseek-flash"


def clamp_direction_proposal(
    proposed: Mapping[str, str],
    rows: Iterable[Mapping[str, object]],
    *,
    min_n: int = 15,
) -> Dict[str, str]:
    """模型点名之后的硬闸：样本不足 15 笔的边丢掉；每币只留一边。"""
    counts: Dict[str, Dict[str, int]] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        side = str(row.get("side") or "").lower()
        if side in ("long", "b"):
            side = "buy"
        elif side in ("short", "s"):
            side = "sell"
        if sym and side in ("buy", "sell"):
            counts.setdefault(sym, {})[side] = int(row.get("n") or 0)
    out: Dict[str, str] = {}
    for sym, side in proposed.items():
        side_l = str(side or "").lower()
        if side_l not in ("buy", "sell"):
            continue
        key = str(sym or "").upper()
        if counts.get(key, {}).get(side_l, 0) < int(min_n):
            continue
        out[key] = side_l
    return out


def ask_direction_flash(rows: Iterable[Mapping[str, object]]) -> tuple:
    """调用 DeepSeek Flash，返回 (block_add, summary, model)。失败则抛错。"""
    import json
    import os

    from backend.services.llm_config_service import LLMConfig, call_llm_api_sync

    facts = summarize_direction(rows, {})
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("没有 DEEPSEEK_API_KEY")
    cfg = LLMConfig(
        id=0,
        name="h626-direction",
        provider="deepseek",
        model=FLASH_MODEL,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        or "https://api.deepseek.com",
        api_key=key,
    )
    system = (
        "你是高频做市模块里的方向判定。只输出一个 JSON 对象，不要其他文字。"
        "block_add 的值只能是 buy 或 sell，表示这一边先不要加仓。"
        "每个币最多停一边。某一边不足 15 笔就不要停。"
        "不要把一个币两边都停掉，也不要整条车道只做多或只做空。"
        "summary 用一两句中文说明为什么。"
    )
    user = (
        "最近 60 分钟各币开仓：\n" + facts +
        "\n输出格式：{\"block_add\": {\"ASTER\": \"sell\"}, \"summary\": \"...\"}。"
        "没有要停的边就让 block_add 为 {}。"
    )
    resp = call_llm_api_sync(
        cfg,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=400,
        response_format={"type": "json_object"},
        timeout=25,
        caller="mm.direction_card",
        bypass_cache=True,
    )
    if not resp:
        raise RuntimeError("deepseek 空响应")
    content = resp["choices"][0]["message"]["content"]
    data = json.loads(content or "{}")
    raw = data.get("block_add") or {}
    if not isinstance(raw, dict):
        raw = {}
    block = clamp_direction_proposal(raw, rows)
    summary = str(data.get("summary") or "").strip() or summarize_direction(rows, block)
    model = str(resp.get("model") or FLASH_MODEL)
    return block, summary, model
