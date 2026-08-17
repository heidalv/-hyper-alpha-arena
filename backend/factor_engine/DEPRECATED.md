# 已废弃目录（2026-08 审计 P2-5）

本目录（backend/factor_engine/）是遗留因子引擎，运行时主引擎为
backend/services/factor_engine/。全库 grep 确认 0 处导入本目录。

- 请勿在本目录新增/修改代码（编辑会落到死代码上，不会生效）。
- 130+ ai_generated 因子（含 0 字节 ai_gen_trend_r2.py）仅作历史归档。
- 清理计划：确认无外部脚本依赖后整体归档删除（M3）。
