"""
M4 — DecisionArbiter：轻量决策日志（P3，2026-04-22）

## 目的
把系统里**所有** "想平仓/减仓/反向" 的请求（不管是 master / defensive / ai_reverse
/ paper_engine 的 SL·TP / profit_protection_manager）全部落一份结构化日志，
便于事后统计:
  - M1 shadow 模式下 "LLM 想 close 但会被拦" 的真实频率
  - M2 冷却拦下了多少次 ai_reverse
  - 各 source 的胜率 / 频次 / 是否重复
  - 哪一层过度敏感 (事后 A/B 对比 用)

## 非目标
- 不拦截、不改变行为（只读事实写日志）
- 不替代 session.events / decision_logs（那些是业务事件，这里是"协调层审计"）

## 输出
data/decision_arbiter.jsonl —— 一行一个 JSON，字段见 CloseRequest 的 to_dict

## 开关
flag `RISK_P3_DECISION_LOG_ENABLED` (default False)

## 使用
```python
from backend.services.decision_arbiter import log_close_request, CloseRequest
log_close_request(CloseRequest(
    symbol="ETH", source="master", reason_intended="master_running_reduce",
    pos_tier="long", pos_side="short", pnl_pct=-0.015, sl_breach=0.3,
    would_block=False, block_rule="",
))
```

不会抛异常（任何写失败都吞掉，换一条记录到 logger）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

# 日志路径（相对项目根）。如果不存在 data/ 目录，用户的 paper 数据库也不能工作，
# 所以我们不建目录，直接失败到 logger。
_LOG_REL_PATH = os.path.join("data", "decision_arbiter.jsonl")


@dataclass(frozen=True)
class CloseRequest:
    """
    "某一层想发起平/减仓" 的单一记录，源码里用它承载本次决策的关键事实。
    """
    symbol: str
    source: Literal[
        "engine_hard",           # paper_engine SL/liq/tp 穿价
        "profit_protection",     # profit_protection_manager
        "staged_tp",             # long_tier_staged_tp
        "master",                # MasterController close/reduce
        "defensive",             # _execute_defensive_verdicts
        "ai_reverse",            # position_memory_manager close_and_open
        "manual",                # API/UI
    ]
    reason_intended: str         # 这一层"打算用"的 close_reason 字符串
    pos_tier: str = ""           # short|mid|long|""
    pos_side: str = ""           # long|short|""
    pnl_pct: float = 0.0         # 浮亏/浮盈 %（负数 = 亏）
    sl_breach: float = 0.0       # SL 穿透率
    confidence: Optional[float] = None
    would_block: bool = False    # 是否被 P3 下层逻辑拦了
    block_rule: str = ""         # 拦截规则 tag（M1/M2/M3/其他）
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = datetime.now(timezone.utc).isoformat()
        return d


def _is_enabled() -> bool:
    try:
        from backend.config.settings import RISK_P3_DECISION_LOG_ENABLED
        return bool(RISK_P3_DECISION_LOG_ENABLED)
    except Exception:
        return False


def log_close_request(req: CloseRequest) -> None:
    """
    落一行 JSONL。任何错误都降级成 logger，不影响交易主路径。
    """
    if not _is_enabled():
        return
    try:
        from backend.utils.jsonl_rotating import append_jsonl
        append_jsonl(_LOG_REL_PATH, req.to_dict())
    except Exception as e:
        # 日志失败不要影响业务；换到 stderr logger
        logger.debug(f"[DecisionArbiter] write failed (ignored): {e}")


def read_recent_lines(n: int = 200) -> list[dict]:
    """调试辅助：读取最近 n 行（不保证行尾完整；空文件返回 []）。"""
    try:
        if not os.path.exists(_LOG_REL_PATH):
            return []
        with open(_LOG_REL_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        out = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    except Exception:
        return []
