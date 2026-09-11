# -*- coding: utf-8 -*-
"""[2026-09-10 第二十四轮] 周报「浮盈回吐验收」小节契约测试。

对应 §23 #10 / §33 / §34：周报必须自动带上「先盈利后大亏」审计，
且审计失败时不得让周报崩掉。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.scripts.midlong_weekly_report import (  # noqa: E402
    render_giveback_section,
    render_report,
)


def _rep():
    return {
        "pattern_n": 3, "pattern_usd": -42.5,
        "by_tier": {
            "mid": {"n": 10, "usd": -20.0, "win_rate": 0.3, "big_loss_n": 4,
                    "pattern_n": 2, "pattern_rate": 0.2, "pattern_usd": -30.0,
                    "giveback_pct_sum": 12.5, "median_giveback_pct": 4.1},
            "long": {"n": 4, "usd": 8.0, "win_rate": 0.5, "big_loss_n": 0,
                     "pattern_n": 1, "pattern_rate": 0.25, "pattern_usd": -12.5,
                     "giveback_pct_sum": 3.0, "median_giveback_pct": 3.0},
        },
        "by_family": {
            "mid_reversion": {"n": 8, "usd": -15.3, "pattern_rate": 0.625,
                              "pattern_usd": -15.3},
            "pro": {"n": 6, "usd": 9.0, "pattern_rate": 0.0, "pattern_usd": 0.0},
        },
        "verdicts": [
            {"name": "mid 模式 USD ≥ 0", "ok": False, "detail": "mid pattern_usd=-30.0"},
            {"name": "门放行集模式率 < 门拦截集", "ok": True, "detail": "allow=0.05 vs block=0.2"},
        ],
    }


def test_render_giveback_section_none_and_error():
    assert render_giveback_section(None) == []
    assert render_giveback_section({}) == []
    err = render_giveback_section({"error": "boom"})
    assert any("审计查询失败" in ln for ln in err)
    # 出错时不渲染其它行
    assert not any("模式笔" in ln for ln in err)


def test_render_giveback_section_content():
    out = render_giveback_section(_rep())
    text = "\n".join(out)
    assert "浮盈回吐验收" in text
    assert "3 笔 / USD -42.50" in text
    assert "[mid] n=10" in text and "模式=2(0.2)" in text
    assert "[long] n=4" in text
    # 最差家族按 pattern_usd 升序，mid_reversion 在 pro 之前
    fam_line = [ln for ln in out if "最差来源家族" in ln][0]
    assert fam_line.index("mid_reversion") < fam_line.index("pro")
    assert "[未达标] mid 模式 USD ≥ 0" in text
    assert "[达标] 门放行集模式率 < 门拦截集" in text


def test_render_report_includes_giveback_section():
    """周报正文必须包含该小节；不传 giveback 时不应出现（向后兼容）。"""
    md = render_report(14, {}, {}, {}, giveback=_rep())
    assert "浮盈回吐验收" in md
    assert "mid 模式 USD ≥ 0" in md
    md2 = render_report(14, {}, {}, {})
    assert "浮盈回吐验收" not in md2


def test_render_report_survives_giveback_error():
    """审计失败（DB 异常等）时周报仍要能生成。"""
    md = render_report(14, {}, {}, {}, giveback={"error": "db down"})
    assert "浮盈回吐验收" in md
    assert "审计查询失败" in md
