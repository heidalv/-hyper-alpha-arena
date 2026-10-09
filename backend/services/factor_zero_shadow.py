# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R17] **被归零因子的前向影子**（只记录，不改变任何信号）。

## 为什么需要（R15/R16 的实测结论）
- IC 学习把 **82/226 个因子**（IC<0）的运行时权重打成 **0.0**；
- 其中 **48%（39 个）的 t = ic·√n 落在 |t|<1**，即与其标准误相比**与 0 无法区分**
  —— 用噪声级的符号做**永久淘汰**，统计上不对称（同为噪声的正 IC 却拿 0.57 权重）；
- 但**改口径的反事实做不了**：`market_analysis_snapshots.indicator_snapshot.factor_contrib`
  **只存被选中的因子**（实测某快照 3 条 / factor_count=10），被归零因子的投票**从未落盘**。
  ⇒ 归零规则把自己从可审计性里也剔除了。

## 本模块做什么
在因子管道**组装完权重、生成信号之后**，把当轮**权重为 0 的因子**及其方向写进
`data/factor_zero_shadow.jsonl`。**不参与任何决策、不改任何信号。**

攒够样本后即可回答（可证伪）：
> 这批被归零的因子，若当时给一个地板权（例如 0.1），
> 复合信号的方向/命中率会更好还是更差？

开关：`FACTOR_ZERO_SHADOW_ENABLED`（**默认 false**，需显式开启；回滚=改回 false 或删键）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PATH = Path("data/factor_zero_shadow.jsonl")
_MAX_LINES = 200000
# [续作R15] 每进程只告警一次：enabled 但本轮无零权因子可记。
_WARNED_EMPTY = False


def enabled() -> bool:
    return os.getenv("FACTOR_ZERO_SHADOW_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _dir_of(signals: Any, name: str) -> Optional[float]:
    try:
        sig = signals.get(name) if isinstance(signals, dict) else None
        if sig is None:
            return None
        d = getattr(sig, "direction", None)
        return None if d is None else round(float(d), 6)
    except Exception:
        return None


def record(symbol: str, weights: Optional[Dict[str, float]], signals: Any,
           regime: str = "") -> None:
    """记录当轮权重为 0 的因子的方向。异常一律吞掉（影子不得影响主流程）。"""
    if not enabled() or not weights:
        return
    try:
        zeros = {}
        for name, w in weights.items():
            try:
                if float(w or 0) == 0.0:
                    d = _dir_of(signals, name)
                    if d is not None and d != 0.0:      # 只记有明确方向的，省体积
                        zeros[name] = d
            except (TypeError, ValueError):
                continue
        if not zeros:
            # [续作R15] 静默返回会掩盖"开关开着却永远采不到样"：
            # 实测（09-25 00:46/00:57 管道正常跑）影子文件仍不存在，原因在此分支——
            # `v3_factor_pipeline` 构造的 `_ic_weights` 只含本符号当轮 `_fvals` 里的因子
            # （`{name: _ic_w.get(name,1.0) for name in _fvals}`），零权因子若不在该集合，
            # 本函数拿不到它们。每进程告警一次，把"没采到样"从静默变成可见。
            global _WARNED_EMPTY
            if not _WARNED_EMPTY:
                _WARNED_EMPTY = True
                logger.warning(
                    "[FactorZeroShadow] 开关已开，但本轮无零权因子可记 "
                    "(weights_keys=%d)。若持续出现，说明零权因子不在本符号当轮的 fvals 中；"
                    "离线反事实可改用 data/factor_runtime_weights.json 的 stats.ic/pred_ic 符号做。",
                    len(weights),
                )
            return
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        # 体积保护：超过上限就轮转一次（保留旧文件为 .1）
        try:
            if _PATH.exists() and _PATH.stat().st_size > 0:
                with _PATH.open("r", encoding="utf-8", errors="replace") as f:
                    n = sum(1 for _ in f)
                if n >= _MAX_LINES:
                    _PATH.replace(_PATH.with_suffix(".jsonl.1"))
        except Exception:
            pass
        row = {"ts": int(time.time() * 1000), "sym": str(symbol or "").upper(),
               "regime": str(regime or ""), "n_zero": len(zeros), "zeros": zeros}
        with _PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover
        logger.debug("[FactorZeroShadow] 记录跳过: %s", exc)


def stats() -> Dict[str, Any]:
    """影子文件现状（供状态报告）。"""
    try:
        if not _PATH.exists():
            return {"enabled": enabled(), "lines": 0, "path": str(_PATH)}
        lines = 0
        facs: Dict[str, int] = {}
        with _PATH.open("r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                lines += 1
                try:
                    row = json.loads(ln)
                    for k in (row.get("zeros") or {}):
                        facs[k] = facs.get(k, 0) + 1
                except Exception:
                    continue
        top = sorted(facs.items(), key=lambda kv: -kv[1])[:5]
        return {"enabled": enabled(), "lines": lines, "distinct_factors": len(facs),
                "top": top, "path": str(_PATH)}
    except Exception as exc:  # pragma: no cover
        return {"enabled": enabled(), "err": str(exc)}
