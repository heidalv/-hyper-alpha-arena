# -*- coding: utf-8 -*-
"""client_order_id 生成与交易所格式适配（v3 方向 2，p2-oms-exec）。

幂等键要同时满足两件相互拉扯的事：
  - **全局唯一**：两笔不同意图绝不能撞 id，否则第二笔会被交易所当重复单静默丢弃；
  - **交易所可接受**：各家对字符集与长度的限制不同，超限会直接拒单。

因此不用「业务字段拼哈希」的确定性方案——那种方案在「同一秒下两笔同参数单」时会撞，
而这在网格 / 分批建仓里是常态。改为随机 uuid，**唯一性由 uuid 保证、幂等性由
先落库后下单的顺序保证**：重试时从库里取回同一个 id，而不是重新算一遍。

各家限制（2026-09 实测/文档）：
  Binance/Aster  ^[\\.A-Z\\:/a-z0-9_-]{1,36}$
  Bybit          ≤36 字符
  OKX            字母数字，≤32 字符（不接受 `-` `_` `.` `:` `/`）
  Gate           需 `t-` 前缀，≤28 字符
统一取**最严交集**：纯字母数字、≤28 字符，前缀便于在交易所后台一眼认出是本系统的单。
"""
from __future__ import annotations

import os
import re
import uuid
from typing import Optional

# 纯字母数字，所有目标交易所都接受
_PREFIX_DEFAULT = "ha"
_MAX_LEN = 28
_SAFE_RE = re.compile(r"[^A-Za-z0-9]")


def id_prefix() -> str:
    """`OMS_CLIENT_ID_PREFIX`：便于在交易所后台区分环境（如 ha / hadev）。"""
    raw = _SAFE_RE.sub("", str(os.getenv("OMS_CLIENT_ID_PREFIX", _PREFIX_DEFAULT) or ""))
    return (raw or _PREFIX_DEFAULT)[:8]


def new_client_order_id(*, account_id: Optional[int] = None) -> str:
    """生成新的幂等键：`<prefix><account><uuid>`，纯字母数字且 ≤28 字符。

    账户号嵌在里面纯粹为了排查方便（对账时一眼看出是哪个账户的单），
    唯一性完全由 uuid 部分保证，不依赖账户号。
    """
    prefix = id_prefix()
    acct = f"{int(account_id)}" if account_id is not None else ""
    acct = _SAFE_RE.sub("", acct)[:6]
    head = f"{prefix}{acct}"
    return f"{head}{uuid.uuid4().hex}"[:_MAX_LEN]


def sanitize_for_exchange(cid: str, exchange: str) -> str:
    """按交易所规则清洗幂等键。Gate 要求 `t-` 前缀，其余用纯字母数字即可。"""
    safe = _SAFE_RE.sub("", str(cid or ""))
    ex = str(exchange or "").lower()
    if ex.startswith("gate"):
        return f"t-{safe}"[:28]
    return safe[:_MAX_LEN]


def is_ours(cid: Optional[str]) -> bool:
    """对账时判断一笔交易所订单是不是本系统下的。

    交易所返回的 clientOrderId 若不是本系统前缀 → 手工下单或其它程序下的，
    对账要单独列出来而不是当成丢失。
    """
    if not cid:
        return False
    s = str(cid)
    if s.startswith("t-"):
        s = s[2:]
    return s.startswith(id_prefix())
