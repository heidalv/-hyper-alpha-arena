# -*- coding: utf-8 -*-
"""诚实性核查：数据中心 4 次重启期间，**交易后端**有没有取数故障？（只读）

目标 ④ 要求"全程保证交易后端取数不中断"——不能默认"没看见问题"，
要按重启窗口逐个统计后端日志里的取数故障特征。
"""
from __future__ import annotations

import io
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "logs" / "backend.log").read_text(
    encoding="utf-8", errors="replace").splitlines()

# DC 四次重启的停机窗口（每次 ±2 分钟）
WINS = [("14:29", "14:34"), ("14:46", "14:52"), ("14:58", "15:04"), ("15:14", "15:20")]
PATS = {
    "DC无数据(取价)": "DC_ONLY 下数据中心无",
    "禁止直连兜底": "禁止直连兜底",
    "K线聚合拒绝(过期)": "拒绝聚合返回",
    "数据过期(stale)": "数据过期",
    "数据中心价格不可用": "数据中心价格不可用",
    "Traceback": "Traceback",
}


def scan(a: str, b: str) -> Counter:
    c = Counter()
    for ln in lines:
        hm = ln[11:16]
        if a <= hm <= b:
            for k, p in PATS.items():
                if p in ln:
                    c[k] += 1
    return c


print("=" * 88)
print("DC 重启窗口内的**后端**取数故障统计")
print("=" * 88)
for a, b in WINS:
    n_rows = sum(1 for ln in lines if a <= ln[11:16] <= b)
    c = scan(a, b)
    detail = dict(c) if c else "无"
    print(f"  {a}-{b}  日志 {n_rows:6d} 行   命中: {detail}")

print("\n对照：14:00-15:20 全窗口")
c = scan("14:00", "15:20")
for k in PATS:
    print(f"  {k:24s} {c.get(k, 0)}")

print("\n判定口径：若窗口内『数据过期/DC无数据/禁止直连兜底』均为 0，")
print("则 DC 重启期间后端**未出现取数失败**（ticker 缓存 TTL 1.5s 无法覆盖 100s 停机，")
print("故这一结论本身也说明后端在这些窗口里没有依赖 DC 实时取数到报错的程度）。")
