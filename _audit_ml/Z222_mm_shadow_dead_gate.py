# -*- coding: utf-8 -*-
"""[§82 核查 2026-09-11 / 待办 P5] MM 影子车道的两道**死闸**：现状、影响量化、接线代价。

清单第 19 条（P5）记录：`check_lane_limits()` 在**生产路径零调用**，于是
  * `toxic_streak`（连续逆选择 → 暂停该币）在运行中的影子里**从未生效**；
  * `daily_loss_stop_pct`（日亏 > 权益 1% → 全停）**全仓无消费方**（从未实现）。

本脚本（只读）：
  ① 复核"零调用点"（AST 级：谁调用 `check_lane_limits`）；
  ② 复核"daily_loss_stop_pct 无判定用消费方"（区分 定义/展示 vs 判定）；
  ③ 量化影响：影子车道（`lane_ledger`）真实表现 + 逆选择笔占比；
  ④ 列出接线的代价清单（改变证据基线 / 需要重启 / 一键回滚方式）。
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

#: 扫描时必须剪掉的目录：`backend/.venv` 里是整棵 site-packages，
#: 第一版没剪 ⇒ rglob 直接卡死（审计脚本自己踩了"看不见的性能陷阱"）。
_PRUNE = {".venv", "venv", ".git", "node_modules", "__pycache__", "_archive",
          "_ai_gen_archive", "_ai_gen_quarantine", "site-packages", "data"}


def _iter_py(base: Path):
    for p in base.rglob("*.py"):
        if any(part in _PRUNE for part in p.parts):
            continue
        yield p


def part_a() -> None:
    print("=" * 100)
    print("① `check_lane_limits` 的调用点（AST 全仓扫描）")
    print("=" * 100)
    hits = []
    for p in _iter_py(ROOT / "backend"):
        try:
            tree = ast.parse(p.read_bytes().decode("utf-8-sig", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "check_lane_limits":
                kind = "test" if "tests" in p.parts else "**生产**"
                hits.append((kind, p.relative_to(ROOT), node.lineno))
    for kind, rel, ln in hits:
        print(f"  {kind:8s} {rel}:{ln}")
    prod = [h for h in hits if h[0] == "**生产**"]
    print(f"  ⇒ 生产调用点 = {len(prod)} 个 {'✅（与清单第 19 条一致）' if not prod else '❗ 需重新判定'}")


def part_b() -> None:
    print()
    print("=" * 100)
    print("② `daily_loss_stop_pct` 的消费方（区分 **判定用** vs 定义/展示）")
    print("=" * 100)
    rows = []
    for p in _iter_py(ROOT / "backend"):
        try:
            src = p.read_bytes().decode("utf-8-sig", errors="replace")
        except Exception:
            continue
        if "daily_loss_stop_pct" not in src:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            if "daily_loss_stop_pct" not in line:
                continue
            s = line.strip()
            code = s.split("#", 1)[0]     # 注释里的 ">" 不算判定（第一版就误判过）
            is_test = "tests" in p.parts or p.name.startswith("test_")
            is_field = bool(re.match(r"^\w+:\s*(float|int|Optional\[float\])", code))
            is_decision = (not is_field) and any(op in code for op in ("<", ">", "if "))
            kind = "测试" if is_test else ("**判定用**" if is_decision else "定义/展示")
            rows.append((kind, p.relative_to(ROOT), i, s[:96]))
    for kind, rel, ln, line in rows:
        print(f"  [{kind}] {rel}:{ln}  {line}")
    decide = [r for r in rows if r[0] == "**判定用**" and "tests" not in str(r[1])]
    print(f"  ⇒ 生产侧**判定用**消费方 = {len(decide)} 个"
          f" {'✅（从未实现，与清单第 19 条一致）' if not decide else '❗'}")


def part_c() -> None:
    print()
    print("=" * 100)
    print("③ 影响量化：影子车道表现（lane_ledger）")
    print("=" * 100)
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        lanes = db.execute(text("""
            select lane_id, count(*) n, round(sum(net_bp)::numeric,1) sum_bp,
                   round(avg(net_bp)::numeric,2) avg_bp,
                   min(ts)::date, max(ts)::date
            from lane_ledger group by lane_id order by n desc
        """)).fetchall()
        for lane, n, sum_bp, avg_bp, d0, d1 in lanes:
            print(f"  {lane:16s} 笔数={n:<5} 累计={sum_bp:>9}bp 均值={avg_bp:>6}bp  {d0}~{d1}")
        mm = db.execute(text("""
            select event, count(*) from lane_ledger
            where lane_id like '%mm%' group by event order by 2 desc limit 8
        """)).fetchall()
        print(f"\n  MM 车道事件分布：{[(e, int(c)) for e, c in mm]}")
        worst = db.execute(text("""
            select symbol, round(net_bp::numeric,2) bp, event, meta_json
            from lane_ledger where lane_id like '%mm%' order by net_bp asc limit 5
        """)).fetchall()
        print("  最差 5 笔：")
        for sym, bp, ev, meta in worst:
            m = {}
            try:
                m = json.loads(meta) if meta else {}
            except Exception:
                pass
            keys = [k for k in ("toxic", "markout_bp", "adverse_bp", "streak", "hold_s") if k in m]
            print(f"    {sym:10s} {bp:>8}bp  {ev:14s} {({k: m[k] for k in keys})}")
        cnt = db.execute(text("""
            select count(*) from lane_ledger where lane_id like '%mm%' and net_bp < -15
        """)).scalar()
        tot = db.execute(text("""
            select count(*) from lane_ledger where lane_id like '%mm%'
        """)).scalar()
        print(f"\n  MM 影子：净 < −15bp 的笔数 = {cnt} / {tot}"
              f"（{100.0*int(cnt or 0)/max(1,int(tot or 0)):.1f}%）"
              " —— 这些正是 `toxic_bp=15` 想拦的逆选择笔")
    finally:
        db.close()


def main() -> int:
    part_a()
    part_b()
    try:
        part_c()
    except Exception as exc:
        print(f"  （DB 段失败：{exc}）")
    print()
    print("=" * 100)
    print("④ 接线代价（供决策 P5）")
    print("=" * 100)
    print("  * 改动面：`runner.plan_tick` 增加一次 `check_lane_limits(...)` 调用，并把"
          "`state.toxic_streak` 作为入参（现成字段，已落库）；`daily_loss_stop_pct` 需新实现")
    print("  * 影响面：**仅影子车道**（`mm_asterdex` mode=paper），不影响实盘/中长线；"
          "但会**改变影子证据基线**（此后更保守，与既有 lane_ledger 不可直接比较）")
    print("  * 回滚：环境变量开关（建议 `MM_LANE_LIMITS_ENFORCE`，默认 false）+ 一次重启")
    print("  * 观察：接线后按 `lane_ledger` 对比「被暂停 tick 数 / 被拦笔数 / 净 bp 变化」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
