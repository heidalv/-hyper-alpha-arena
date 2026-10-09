# -*- coding: utf-8 -*-
"""[F378 2026-09-18] 主脑看到的"近 14 天战绩"是**毛口径**（未扣费用），比其他地方乐观。

## 事实链

1. `mlto/brain.py:889-899` 查询：
   ```sql
   SELECT side, size, entry_price, close_price, partial_realized_pnl, closed_at
   FROM paper_positions WHERE symbol=:sym AND status='closed' AND closed_at >= now() - interval
   ```
   —— **没有取费用列**。而 `paper_positions` 实际有 **`partial_fee_paid`**（information_schema 实测）。
2. 计算式（`:910`）：`net = (close - entry) * direction * size + partial_realized_pnl`
   —— **price×size 的毛差 + 部分已实现**，**不扣任何费用**。
3. 该系统里**已有净口径表**：`trade_facts`（含 `fees`，且报告历史审计 §43 曾把
   "逐笔 P&L 不含交易费"作为已修项、引入 `pnl_basis` 唯一口径源）。
4. **实测差距**（近 14 天）：`trade_facts` 净 pnl 合计 **−391.93**、费用合计 **38.68**
   ⇒ 毛口径 ≈ **−353.25**；**费用占毛盈亏 11.0%**。
5. 该 bundle 由 `build_feed()` 放进 `extras["recent_pnl_14d"]` / `["recent_same_dir_pnl_14d"]`，
   **每轮进主脑 prompt**（运行态实测 BTC 10 笔 / ETH 12 笔 / SOL 15 笔，样本非空）。

## 影响方向（重要）

省略费用**永远偏乐观**：亏损看起来更小、盈利看起来更大。主脑据此判断"最近打得怎么样"
⇒ 与系统其它地方的净口径（`trade_facts.pnl`）**系统性不一致**，
且不一致的方向是"让主脑高估自己"。

## 结论

这是"`pnl_basis` 唯一口径源"那次修复**未覆盖到的一条路径**——
属本报告主题"写了/修了，但没覆盖全链路"的又一例。
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import brain as B  # noqa: E402


def test_query_omits_fee_column():
    """查询列里没有费用列 ⇒ 无法算出净口径（修好时本用例失败）。"""
    src = inspect.getsource(B._recent_same_dir_pnl)
    m = re.search(r"SELECT(.+?)FROM\s+paper_positions", src, re.S | re.I)
    assert m, "未找到查询语句"
    cols = m.group(1)
    assert "partial_fee_paid" not in cols, (
        "查询已包含费用列 ⇒ 口径可能已修为净，请更新报告 §34 与待办 B17")
    assert "fees" not in cols.lower(), "查询已含 fees ⇒ 请更新 §34"
    for need in ("entry_price", "close_price", "size"):
        assert need in cols, f"缺列 {need}（查询结构变了，请复核 §34）"


def test_net_formula_has_no_fee_deduction():
    """净盈亏公式不含费用项。"""
    src = inspect.getsource(B._recent_same_dir_pnl)
    assert "partial_realized_pnl" in src
    line = next((ln for ln in src.splitlines() if "net =" in ln), "")
    assert line, "未找到 net = 计算式"
    for kw in ("fee", "commission", "cost"):
        assert kw not in line.lower(), f"公式已含『{kw}』⇒ 可能已修为净，请更新 §34"


def test_paper_positions_actually_has_fee_column():
    """反证：费用列**存在**，所以"取不到"不是理由（只是没取）。"""
    from sqlalchemy import text
    from backend.database.connection import SessionLocal
    try:
        db = SessionLocal()
        db.execute(text("SET app.is_admin='on'"))
        names = [r[0] for r in db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='paper_positions'")).fetchall()]
    except Exception:
        pytest.skip("无 DB 会话，无法反证列存在")
    finally:
        try:
            db.close()
        except Exception:
            pass
    assert any("fee" in n.lower() for n in names), (
        "paper_positions 已无费用列 ⇒ 本结论前提变了，请复核 §34")


def test_bundle_enters_brain_feed():
    """该 bundle 确实进了 build_feed 的 extras（不是算了没人读）。"""
    src = inspect.getsource(B.build_feed)
    assert '"recent_pnl_14d": pnl_bundle' in src
    assert '"recent_same_dir_pnl_14d"' in src


def test_gross_vs_net_gap_is_material():
    """量化差距：近 14 天 trade_facts 的费用占毛盈亏比例（记录性断言，无 DB 时跳过）。"""
    from sqlalchemy import text
    from backend.database.connection import SessionLocal
    try:
        db = SessionLocal()
        db.execute(text("SET app.is_admin='on'"))
        r = db.execute(text(
            "SELECT SUM(pnl), SUM(fees) FROM trade_facts "
            "WHERE ts >= now() - interval '14 days'")).fetchone()
    except Exception:
        pytest.skip("无 DB 会话")
    finally:
        try:
            db.close()
        except Exception:
            pass
    net, fees = (r[0] or 0.0), (r[1] or 0.0)
    if abs(net) < 1e-9:
        pytest.skip("窗口内无成交")
    share = abs(fees) / max(abs(net + fees), 1e-9)
    assert share < 0.5, f"费用占毛盈亏 {share:.1%} —— 异常偏高，请复核 §34"
    # 打印量级供人工核对（不在断言里锁定具体数值，避免随行情变动而红）
    print(f"[F378] 近14天 净={net:.2f} 费用={fees:.2f} 毛={net + fees:.2f} 费用占比={share:.1%}")



# ───────── F387（2026-09-18）：主脑"复盘记忆"丢掉 `exit` 字段（结构性恒空） ─────────
# 本文件主题是"主脑饲料的口径/内容是否如实"，F387 同族：字段一直在库里，但被静默丢弃。

def test_f387_reflexion_exit_guard_is_never_true():
    """`brain.py:980` 的 `isinstance(r, dict)` 对 `RowMapping` **恒为 False** ⇒ `exit` 恒空串。

    证据（脚本 `scripts/probe_reflexion_exit_field.py` 实测）：
    - 同一函数用 `.mappings().all()` ⇒ 行类型 `sqlalchemy.engine.row.RowMapping`；
    - `isinstance(r, dict)` = **False**，而 `isinstance(r, collections.abc.Mapping)` = **True**；
    - 库里 `close_reason` **10/10 非空**（`thesis_should_close`、`trend_weaken: …`、
      `exit_policy:min_roi_decay`、`breakeven_tp`、`profit_drawdown_full`）⇒ 属**真实信息丢失**。
    修好（改用 Mapping 判断或直接取值）时本用例失败，提醒同步报告 §42 与待办 B22。
    """
    src = (ROOT / "backend" / "services" / "mlto" / "brain.py").read_text(encoding="utf-8")
    assert '"exit": str(r.get("close_reason") or "")[:40] if isinstance(r, dict) else ""' in src, (
        "F387 的写法已变 ⇒ 请复核 §42（若已修好，本断言与文档都要更新）")
    # 同一函数确实从 mappings 取行（这正是判断失效的原因）
    i = src.index("def _reflexion_memory")
    seg = src[i:i + 2200]
    assert ".mappings().all()" in seg, "取行方式变了 ⇒ 必须重新判定 isinstance 是否仍失效"


def test_f387_rowmapping_is_not_a_dict():
    """把"为什么失效"钉成**可执行**断言（不依赖 DB）：Mapping 行不是 dict。"""
    import collections.abc as abc
    try:
        from sqlalchemy.engine.row import RowMapping
    except Exception:  # noqa: BLE001
        pytest.skip("无 sqlalchemy")
    assert issubclass(RowMapping, abc.Mapping), "RowMapping 应为 Mapping"
    assert not issubclass(RowMapping, dict), (
        "若 RowMapping 变成 dict 子类，F387 的失效前提消失 ⇒ 复核 §42")
