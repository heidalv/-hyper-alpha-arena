@echo off
REM [Sec.81 / decision P23-A 2026-09-11] Daily read-only audit:
REM   1) midlong_audit_suite.py  (9 read-only checks + snapshot)
REM   2) _audit_ml/Z221_fix_effect_slice.py --json   (before/after economics + P26 recheck)
REM   3) _audit_ml/Z237_suppression_counterfactual.py (suppression counterfactual proxy)
REM   4) _audit_ml/Z260_p29_recheck.py (P29-C long SL/risk effect recheck; NOT_READY until samples)
REM   5) _audit_ml/Z272_objective_verification.py (objective 1-4 verification matrix; PENDING gates)
REM Scheduled task: "HyperAlpha-MidlongAudit" at 09:05 daily.
REM IMPORTANT: keep this file ASCII-only. cmd.exe reads .cmd in the OEM codepage;
REM non-ASCII text here breaks line parsing (seen 2026-09-11: Chinese echo lines
REM split into bogus commands). All Chinese labels are printed by the Python tools.
setlocal
set PYTHONIOENCODING=utf-8
cd /d D:\001Alpha\Hyper-Alpha-Arena
echo ==================== %DATE% %TIME% ====================
echo [1/4] midlong_audit_suite >> "logs\audit_suite_daily.log" 2>&1
".venv\Scripts\python.exe" backend\scripts\midlong_audit_suite.py --timeout 900 >> "logs\audit_suite_daily.log" 2>&1
echo [2/4] fix_effect_slice >> "logs\audit_suite_daily.log" 2>&1
".venv\Scripts\python.exe" _audit_ml\Z221_fix_effect_slice.py --json >> "logs\audit_suite_daily.log" 2>&1
echo [3/4] suppression_counterfactual >> "logs\audit_suite_daily.log" 2>&1
".venv\Scripts\python.exe" _audit_ml\Z237_suppression_counterfactual.py 30 >> "logs\audit_suite_daily.log" 2>&1
echo [4/6] p29_recheck >> "logs\audit_suite_daily.log" 2>&1
".venv\Scripts\python.exe" _audit_ml\Z260_p29_recheck.py >> "logs\audit_suite_daily.log" 2>&1
echo [5/6] objective_matrix >> "logs\audit_suite_daily.log" 2>&1
".venv\Scripts\python.exe" _audit_ml\Z272_objective_verification.py >> "logs\audit_suite_daily.log" 2>&1
echo [6/6] preregistered_verdict >> "logs\audit_suite_daily.log" 2>&1
".venv\Scripts\python.exe" _audit_ml\Z276_preregistered_verdict.py >> "logs\audit_suite_daily.log" 2>&1
echo exit=%ERRORLEVEL%
endlocal
