# -*- coding: utf-8 -*-
"""轮113 归因链路纪律回归测试（2026-09-19）。

## 本轮更正了什么

轮112 用 `brain_theses` 做"LLM 置信度 → 结果"的归因，得出 corr=−0.06。
**数据源选错了**：

  · `alpha_arena.brain_theses` = 委员会影子落库表，实测 1069 行、source 全为
    `committee_shadow`、`created_at/updated_at` 停在 **2026-08-31** ⇒ 冻结快照；
  · 它与活表**共用同一批 thesis_id** ⇒ "positions ⋈ brain_theses" 能查出结果，
    但拿到的是 **8 月的陈旧置信度**（10/20/30/45/50）；
  · 活表是 `alpha_analytics.mlto_thesis`（**另一个库**，跨库 join 不可用）。

用活表重算：71 笔 → 9 个 thesis，conviction 33–52，corr = **+0.076**（仍无预测力，
但这次是**正确数据源**上的结论）。同时更正"39% 覆盖率"：那是 30 天窗口混了
09-05 之前的**接线前**历史；09-06 之后按天覆盖率是 **100%**。

本测试把这条纪律钉住：活表必须新鲜、覆盖必须能解析、死表必须被标注。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ══════════════════════════════════════════════════════════════════════
# ① 代码里必须写明"哪张表是活的、哪张是死的"
# ══════════════════════════════════════════════════════════════════════

def test_mlto_thesis_model_documents_the_trap():
    src = open(os.path.join(_ROOT, "backend/services/mlto/db_models.py"), encoding="utf-8").read()
    i = src.index("class MltoThesis(")
    doc = src[i: i + 1600]
    assert "活表" in doc
    assert "brain_theses" in doc and "冻结快照" in doc
    assert "两次查询" in doc, "必须写明跨库 ⇒ 不能 SQL join"
    assert "复用并原地更新" in doc, "必须写明 9 thesis ↔ 71 笔是正常的"


def test_committee_shadow_writer_warns_not_to_use_it_for_attribution():
    src = open(os.path.join(_ROOT, "backend/services/mlto/committee_shadow.py"),
               encoding="utf-8").read()
    i = src.index("落库 brain_theses")
    block = src[i: i + 1600]
    assert "已停写" in block and "不要再拿它做归因" in block
    assert "mlto_thesis" in block


# ══════════════════════════════════════════════════════════════════════
# ② 死表确实死了（这是"别用它"的事实依据）
# ══════════════════════════════════════════════════════════════════════

def test_brain_theses_is_frozen():
    from sqlalchemy import text

    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("select set_config('app.is_admin','on',false)"))
        row = db.execute(text("""
            SELECT COUNT(*), MAX(created_at), COUNT(DISTINCT source)
            FROM brain_theses""")).fetchone()
    finally:
        db.close()
    if not row or not row[0]:
        pytest.skip("brain_theses 为空（表已清理）")
    n, last, n_src = int(row[0]), row[1], int(row[2] or 0)
    assert n > 0
    # 停写日期是事实依据：新写入会推进它，届时本用例变红，提醒更新文档
    assert str(last)[:10] == "2026-08-31", f"brain_theses 又有新写入（{last}）—— 请复核文档"
    assert n_src == 1, "source 应只剩 committee_shadow"


# ══════════════════════════════════════════════════════════════════════
# ③ 活表：新鲜 + 可解析（现场，取不到就跳过）
# ══════════════════════════════════════════════════════════════════════

def test_live_thesis_store_is_fresh_and_resolvable():
    from sqlalchemy import text

    from backend.database.connection import AnalyticsSessionLocal, SessionLocal

    core = SessionLocal()
    try:
        core.execute(text("select set_config('app.is_admin','on',false)"))
        rows = core.execute(text("""
            SELECT DISTINCT exit_state_json::json->'open_metadata'->>'thesis_id' AS tid
            FROM paper_positions
            WHERE timeframe_tier='mid' AND opened_at >= now() - interval '3 days'
              AND exit_state_json::json->'open_metadata'->>'thesis_id' IS NOT NULL""")).fetchall()
    finally:
        core.close()
    tids = [str(r[0]) for r in rows if r[0]]
    if not tids:
        pytest.skip("近 3 天没有带 thesis_id 的中线开仓（非失败）")

    ana = AnalyticsSessionLocal()
    try:
        got = ana.execute(text(
            "SELECT COUNT(*), MAX(updated_at) FROM mlto_thesis WHERE thesis_id = ANY(:t)"),
            {"t": tids}).fetchone()
    finally:
        ana.close()
    assert int(got[0]) == len(tids), f"活表解析率 {got[0]}/{len(tids)}"
    assert got[1] is not None


def test_recent_open_metadata_always_has_thesis_id():
    """09-06 接线之后：**AI 主脑臂**（entry_source=mlto）的 thesis_id 覆盖率必须是 100%。

    例外（已核实，不算缺口）：`factor_route` 开的是"脑没在分析的币"，
    那一刻 `thesis_store.get()` 本来就取不到 thesis —— 实测唯一一例是
    #4691 DOT（2026-09-16 23:04，factor_route，open_metadata 只有 session_id/tier/source）。
    该路径已在轮109 关掉实开（影子档），所以这类缺口不会再新增。
    """
    from sqlalchemy import text

    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("select set_config('app.is_admin','on',false)"))
        rows = db.execute(text("""
            SELECT COALESCE(exit_state_json::json->>'entry_source','(无)') AS src,
                   COUNT(*),
                   COUNT(exit_state_json::json->'open_metadata'->>'thesis_id')
            FROM paper_positions
            WHERE timeframe_tier='mid'
              AND opened_at >= GREATEST(now() - interval '7 days',
                                        TIMESTAMP '2026-09-06 00:00:00')
            GROUP BY 1""")).fetchall()
    finally:
        db.close()
    if not rows:
        pytest.skip("窗口内没有中线开仓（非失败）")
    stats = {str(r[0]): (int(r[1]), int(r[2])) for r in rows}
    mlto = stats.get("mlto")
    assert mlto is not None, f"窗口内没有 mlto 臂样本: {stats}"
    assert mlto[0] == mlto[1], f"mlto 臂覆盖率 {mlto[1]}/{mlto[0]} ≠ 100%"
    # factor_route 允许缺口，但必须在 1 笔以内（轮109 后不再新增）
    fr = stats.get("factor_route")
    if fr:
        assert fr[0] - fr[1] <= 1, f"factor_route 缺口扩大: {stats}"


def test_historical_gap_is_before_the_wiring():
    """更正 39% 那个数字：缺口在 09-05 及之前，09-06 起按天 100%。"""
    from sqlalchemy import text

    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("select set_config('app.is_admin','on',false)"))
        row = db.execute(text("""
            SELECT COUNT(*) AS n,
                   COUNT(exit_state_json::json->'open_metadata'->>'thesis_id') AS with_tid
            FROM paper_positions
            WHERE timeframe_tier='mid'
              AND opened_at >= now() - interval '30 days'
              AND opened_at <  TIMESTAMP '2026-09-06 00:00:00'""")).fetchone()
    finally:
        db.close()
    n = int(row[0])
    if n == 0:
        pytest.skip("30 天窗口已不含接线前数据（非失败）")
    assert int(row[1]) == 0, "接线前那批本应无 thesis_id（若有了请更新文档）"
