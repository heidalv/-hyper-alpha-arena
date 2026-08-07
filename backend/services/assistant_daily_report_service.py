"""Alpha 助手日报 / 总结报表文本（Web + 飞书推送）。"""

from __future__ import annotations

from typing import Any, Dict


def build_daily_report_text(*, window_hours: int = 24) -> str:
    from backend.services.health_snapshot_service import build_combined_digest

    combined = build_combined_digest(window_hours=window_hours)
    digest = combined.get("log_digest") or {}
    health = combined.get("health_snapshot") or {}
    apis = health.get("apis") or {}
    funnel = (apis.get("proposal_funnel") or {}).get("data") or {}
    f = funnel.get("funnel") or {}
    oc = (apis.get("opencode_status") or {}).get("data") or {}

    lines = [
        f"**Alpha 助手日报（{window_hours}h）**",
        "",
        f"- 后台 ERROR：**{int(digest.get('total_errors') or 0)}** 条 / "
        f"{int(digest.get('distinct_groups') or 0)} 类（P0: {int(digest.get('p0_count') or 0)}）",
        f"- 健康检查：{health.get('ok_count', 0)}/{health.get('total', 0)} 通过",
        f"- OpenCode Sidecar：{'正常' if oc.get('serve_healthy') else '异常'}",
        f"- 进化漏斗：创建 {f.get('created', 0)} → 已评估 {f.get('evaluated', 0)} → 改善 {f.get('improved', 0)}",
    ]
    for item in (digest.get("entries") or [])[:3]:
        lines.append(
            f"  · [{item.get('severity_hint', 'P2')}] {item.get('logger', '?')} ×{item.get('count', 0)}"
        )
    lines.append("\n可在 OpenCode 智能中心查看详情。")
    return "\n".join(lines)


def build_daily_report_payload(*, window_hours: int = 24) -> Dict[str, Any]:
    text = build_daily_report_text(window_hours=window_hours)
    return {"title": "Alpha 助手日报", "text": text, "window_hours": window_hours}
