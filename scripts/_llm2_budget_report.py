"""U1-3 每日 LLM 预算报告（只读）。用法: python scripts/_llm2_budget_report.py"""
import json
import sys

sys.path.insert(0, ".")
from backend.services.llm_budget_governor import llm2_report  # noqa: E402

if __name__ == "__main__":
    r = llm2_report()
    print(json.dumps(r, ensure_ascii=False, indent=2))
