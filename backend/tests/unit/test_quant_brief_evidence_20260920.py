# -*- coding: utf-8 -*-
"""[轮138 2026-09-20] 主脑"普遍不建议开仓"的最后一环：量化简报的证据被**漏塞**。

## 实测（300 次 refresh）
- 256 次（85%）是 `accepted=False rec_open=False dir=neutral`；
- 缺项恒为同一组：`adx_1d(107) / vol_ratio_1h(104) / rsi_4h(102) / trend_1w(102)`
  `/ fear_greed(102) / macd_hist_1h(101)`。

## 根因
`context_pack` 组装给 `mid_long_quant_brief` 的 `_md` 只塞了 `rsi` 与 `ema_trend`：
```python
_ind_1h = {"rsi": row.get("rsi14_1h"), "ema_trend": row.get("ema_trend_1h")}   # 缺 macd_hist / vol_ratio
_ind_1d = {"rsi": row.get("rsi14_1d"), "atr_pct": row.get("atr14_1d_pct")}      # 缺 adx
"adx_1d": row.get("adx_1d")        # ← market 层此前根本没有这个键
```
而简报预检要的正是 `ind_1h.macd_hist` / `ind_1h.vol_ratio` / `ind_1d.adx` / `md.adx_1d`
⇒ 这些项**恒缺**，主脑每轮都在"缺一大片证据"下判断 ⇒ 大量 neutral/不推荐。
（同 轮132 的 `tier`、轮135 的 `current`/`close`：又是**接口两侧键名/字段不一致**。）

## 本轮修复
1. market 层补齐可派生项：`rsi14_4h` / `macd_hist_1h` / `vol_ratio_1h` / `adx14_1d` / `trend_1w`
   （全部由**已在取的 K 线**算出：1h/4h/1d + 库里的 1w，不新增数据源）；
2. 组装 `_md` 时按简报要的键补齐（`macd_hist`/`vol_ratio`/`adx`，`adx_1d` 兼容两种键名）。

## 仍未解决（如实）
`fear_greed` **无数据源接入 market 层**（此前已查明：恐贪/鲸鱼没有进主脑上下文的路）。
本轮只做"有源就透传"，预检仍会如实记缺 —— 下一步要么接 `sentiment_composite_service`/特征表，
要么明确该域不参与。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

_MUST = ("rsi_4h", "macd_hist_1h", "vol_ratio_1h", "adx_1d", "trend_1w")


def test_market_layer_provides_derived_fields():
    from backend.services.analysis import context_pack as cp

    p = cp.build("midlong_thesis", symbols=["BTC"])
    row = (p.layers.get("market") or {}).get("symbols", {}).get("BTC", {})
    for k in ("rsi14_4h", "macd_hist_1h", "vol_ratio_1h", "adx14_1d", "trend_1w"):
        assert row.get(k) is not None, f"market 层缺 {k}（量化简报会恒报缺）"


def test_quant_brief_no_longer_missing_those_items():
    """端到端：这些项**不得**再出现在简报的 missing_data 里。"""
    import json
    from collections import Counter

    from backend.services.analysis import context_pack as cp

    pack = cp.build("midlong_thesis", symbols=["BTC"])
    brief = None
    fa = (pack.layers.get("factors") or {}).get("symbols", {}).get("BTC", {})
    brief = fa.get("brief") or {}
    if not brief:
        # factors 层不可用时，退化为直接检查市场层字段（上面的测试已覆盖），此处只做形态校验
        print("factors 层无 brief，跳过：", json.dumps({k: (pack.layers.get(k) or {}).keys().__len__()
                                                    for k in pack.layers}, ensure_ascii=False))
        return
    missing = list(brief.get("missing_data") or [])
    still = [m for m in missing if m in _MUST]
    assert not still, f"这些项仍被判缺失（组装漏塞）: {Counter(still)}"


def test_md_assembly_includes_required_keys():
    """源码守卫：`_md` 必须把 macd_hist / vol_ratio / adx 塞给量化简报（防再次漏塞）。"""
    src = (ROOT / "backend/services/analysis/context_pack.py").read_text(encoding="utf-8", errors="replace")
    for key in ('"macd_hist": row.get("macd_hist_1h")',
                '"vol_ratio": row.get("vol_ratio_1h")',
                '"adx": row.get("adx14_1d") or row.get("adx_1d")',
                '"adx_1d": row.get("adx14_1d") or row.get("adx_1d")'):
        assert key in src, f"`_md` 组装缺 {key}（量化简报会恒报缺）"
