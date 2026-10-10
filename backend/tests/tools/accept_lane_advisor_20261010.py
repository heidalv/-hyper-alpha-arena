# -*- coding: utf-8 -*-
"""[2026-10-10] 验收 2/3/4（真依赖，只出提案）：

  2. 问「吃单进场能不能打开」→ 检索命中「Aster 挂单 0 费/吃单约 4bp」卡片 + 我们自己的
     亏单教训，模型应拒绝（或至少不许落地）。
  3. 没写知识条目编号 / 没过统计关 / 只出提案 ⇒ 参数文件**字节不变**（对比 hash）。
  4. 模型 / 知识库不可用 ⇒ 交易照旧（只写 hold 日志）。

真调 LLM（DeepSeek，项目既有通道）+ 真 RAG（本地 Chroma）。`MM_BRAIN_APPLY=0`。
用法：.venv\\Scripts\\python.exe backend\\tests\\tools\\accept_lane_advisor_20261010.py
"""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

os.environ["MM_BRAIN_APPLY"] = "0"          # 只出提案
os.environ["MM_BRAIN_MAX_GAP_S"] = "0"      # 绕过节流（验证时立即跑）

from backend.services.knowledge_lane_retriever import retrieve_for_lane  # noqa: E402
from backend.services.market_maker import brain_advisor as BA  # noqa: E402

PARAMS = ROOT / "data" / "flow_learn_params.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else "(missing)"


def main() -> int:
    print("=" * 78)
    print("验收 2：知识库检索「吃单进场」相关依据")
    kb = retrieve_for_lane("吃单进场 taker entry 费率 成本 挂单", "hft", top_k=12)
    print("  degraded:", kb["degraded"], "| reason:", kb["reason"])
    print("  卡片编号:", kb["card_ids"])
    # 判据：把 chunk 拼起来做关键词命中（不假设排序 —— 向量排序是 embedding 的事）
    blob = "\n".join((c.get("text") or "") for c in kb["cards"])
    hit_keywords = [k for k in ("吃单约 4bp", "挂单（maker）手续费为 0", "吃单（taker）约 4bp")
                    if k in blob]
    print("  命中关键词:", hit_keywords)
    print("  ↳ 命中卡（含 aster-fees）:",
          [c["card_id"] for c in kb["cards"] if "aster-fees" in c["card_id"]])
    fee_ok = bool(hit_keywords) and any(
        "aster-fees" in c["card_id"] for c in kb["cards"])
    print("  验收 2a（费率事实已入检索结果）:", fee_ok)
    print("  本仓教训条数:", len(kb["lessons"]),
          "（教训由主脑按笔自动生成，样本多起来后自然非空）")
    print("  冲突口径:", kb["conflict_rule"])

    before = _sha(PARAMS)
    print()
    print("=" * 78)
    print("验收 3+4：跑真一轮（真 LLM + 真 RAG，MM_BRAIN_APPLY=0）")
    res = BA.run_lane("hft", root=ROOT,
                      query="吃单进场能不能打开？现在的挂单费率与吃单成本是多少？")
    print("  decision:", res["decision"])
    print("  why:", res["why"])
    print("  kb:", json.dumps(res.get("kb"), ensure_ascii=False))
    print("  gate:", json.dumps(res.get("gate"), ensure_ascii=False))
    print("  raw_llm:", (res.get("raw_llm") or "")[:400])
    if res.get("cited"):
        print("  cited:", res["cited"], "param:", res.get("param"),
              f"{res.get('old')} -> {res.get('new')}")

    after = _sha(PARAMS)
    print()
    print(f"  flow_learn_params.json hash: {before} -> {after}",
          "（不变 ✓）" if before == after else "（!! 变了）")
    jpath = ROOT / BA.ADVISOR_JOURNAL
    print("  审计日志:", jpath.exists(), "| 末条:",
          json.dumps(json.loads(jpath.read_text(encoding="utf-8").strip().splitlines()[-1]),
                     ensure_ascii=False)[:300] if jpath.exists() else "-")
    # 验收 3：只出提案/被拒/过不了关 ⇒ 参数文件字节不变（模型说 hold 也算这一类）
    ok = before == after and fee_ok and res["decision"] in (
        "hold", "rejected", "blocked_by_gate", "blocked_by_counterfactual",
        "proposal_only", "kb_unavailable", "not_due")
    print()
    print("验收 2/3 结论:", "通过" if ok else "未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
