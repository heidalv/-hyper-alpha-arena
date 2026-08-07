import re
from collections import Counter

print("=" * 60)
print("后端日志深度分析")
print("=" * 60)

with open("backend.log", "r", encoding="utf-8", errors="ignore") as f:
    logs = f.read()

# 1. 日志级别统计
print("\n[1] 日志级别分布")
print("-" * 40)
levels = {
    "ERROR": len(re.findall(r"\bERROR\b", logs)),
    "WARNING": len(re.findall(r"\bWARNING\b", logs)),
    "INFO": len(re.findall(r"\bINFO\b", logs)),
    "DEBUG": len(re.findall(r"\bDEBUG\b", logs)),
}
total_logs = sum(levels.values())
for level, count in sorted(levels.items(), key=lambda x: -x[1]):
    pct = 100.0 * count / total_logs if total_logs > 0 else 0
    print(f"{level:10s}: {count:6d} ({pct:5.1f}%)")

# 2. 异常统计
print("\n[2] 关键错误统计")
print("-" * 40)
exception_count = len(re.findall(r"Exception|Traceback", logs))
timeout_count = len(re.findall(r"(?i)timeout", logs))
unicode_errors = len(re.findall(r"UnicodeEncodeError|UnicodeDecodeError", logs))
connection_errors = len(re.findall(r"ConnectionError|ConnectionRefused", logs))
sqlalchemy_errors = len(re.findall(r"(sqlalchemy|SQLAlchemy|OperationalError)", logs))
print(f"Exception/Traceback: {exception_count}")
print(f"Timeout: {timeout_count}")
print(f"Unicode Errors: {unicode_errors}")
print(f"Connection Errors: {connection_errors}")
print(f"SQLAlchemy Errors: {sqlalchemy_errors}")

# 3. 搜索整改代码关键字
print("\n[3] 整改代码运行日志检查")
print("-" * 40)
keywords = {
    "reduce_cooldown/冷却": r"(?i)(reduce_cooldown|冷却)",
    "defensive_tiered/分层": r"(?i)(defensive_tiered|分层)",
    "decision_interval": r"(?i)decision_interval|MIN_DECISION_INTERVAL",
    "inherit_lessons": r"(?i)inherit_lessons",
    "update_partial_close": r"(?i)update_partial_close_memory",
    "_get_validated_trade_nature": r"(?i)_get_validated_trade_nature|trade_nature",
    "delta/增量推送": r"(?i)(delta|增量)",
    "defensive模式": r"(?i)defensive",
}

for name, pattern in keywords.items():
    count = len(re.findall(pattern, logs))
    print(f"{name:30s}: {count:4d}")

# 4. AI决策相关
print("\n[4] AI决策相关日志")
print("-" * 40)
llm_calls = len(re.findall(r"(?i)(call_llm|llm_call|llm)", logs))
full_auto = len(re.findall(r"\[FullAuto\]|\[FULLAUTO\]", logs))
decision_verdict = len(re.findall(r"(?i)(decision|verdict)", logs))
print(f"LLM 调用相关: {llm_calls}")
print(f"FullAuto 日志: {full_auto}")
print(f"Decision/Verdict: {decision_verdict}")

# 5. 性能相关
print("\n[5] 性能指标")
print("-" * 40)
slow_ops = len(re.findall(r"(\d{3,}ms|timeout)", logs))
db_pool = len(re.findall(r"(?i)(pool|connection)", logs))
memory_warning = len(re.findall(r"(?i)(memory|oom|out of)", logs))
print(f"慢操作(>=100ms): {slow_ops}")
print(f"数据库连接池: {db_pool}")
print(f"内存警告: {memory_warning}")
