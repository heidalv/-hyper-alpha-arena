# -*- coding: utf-8 -*-
"""[§90 修复 2026-09-11 / 缺陷 #73/#75]（包内规范位置） 审计口径的**账户隔离**：默认只统计活跃 PAPER 账户。

现场：`research` 层 30 天 −$413.08 落在账号 **147/149**（`accounts.name = '[已归档-测试残留] …'`）；
另有一个短期实验账号 **#156「150u」**（8/18–8/22）也在库里。此前我的 mid/long 数字
按层过滤但**没按账户过滤** ⇒ 19 笔 / −$14.62 的测试残留混进了"中长线净额"。

口径（本文件即唯一真相源）：
  * `AUDIT_ACCOUNT_ID`（环境变量，默认 **14 = 「小资金」PAPER**）；
  * `account_clause(alias="")` 返回可直接拼进 SQL 的片段；设为 0 表示"不隔离"（仅供对照）。
"""
from __future__ import annotations

import os
from typing import Optional

#: 活跃 PAPER 账户（「小资金」）；0 = 不做账户隔离（只用于对照实验）
DEFAULT_ACCOUNT_ID = 14


def account_id() -> int:
    try:
        v = int(float(os.environ.get("AUDIT_ACCOUNT_ID", DEFAULT_ACCOUNT_ID) or 0))
    except (TypeError, ValueError):
        v = DEFAULT_ACCOUNT_ID
    return v


def account_clause(alias: str = "", *, column: str = "account_id") -> str:
    """返回 ` and account_id = 14` 之类的 SQL 片段（`id<=0` 时返回空串）。

    `alias` 用于多表/别名场景（如 `p.account_id`）。
    """
    aid = account_id()
    if aid <= 0:
        return ""
    prefix = f"{alias}." if alias else ""
    return f" and {prefix}{column} = {aid}"


#: 已归档/测试残留账户（供报告与排查引用；不参与业绩口径）
ARCHIVED_ACCOUNT_IDS = (147, 149)
EXPERIMENT_ACCOUNT_IDS = (156,)


def describe_scope() -> str:
    aid = account_id()
    if aid <= 0:
        return "账户口径：**未隔离**（AUDIT_ACCOUNT_ID=0，仅用于对照）"
    extra = ""
    if aid in ARCHIVED_ACCOUNT_IDS:
        extra = " ⚠️ 该账户名含『已归档-测试残留』，不应作为业绩口径"
    elif aid in EXPERIMENT_ACCOUNT_IDS:
        extra = " ⚠️ 短期实验账户（8/18–8/22），不应作为业绩口径"
    return f"账户口径：account_id={aid}{extra}"
