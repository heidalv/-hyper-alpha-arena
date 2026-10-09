' ============================================================
' [h768b 2026-10-03] start-backend-hidden.vbs(基础解释器直启版)
'
' [2026-10-09 崩溃循环根治] 改两处:
'   1. 启动器换成 **.venv\Scripts\python.exe**(经 cmd 路径反复验证有效:
'      .runtime python 被 wscript SW_HIDE 启动时**不落地**——与数据中台
'      vbs 启动失败的既有记录同款症状:"vbs 启动不落地 / 进程从未起来")。
'      venv stub 会派生 .runtime 子进程并弹一个最小化控制台(可接受的代价)。
'   2. 入口换成 run_uvicorn_tracked.py:
'      · 端口守卫(8000 已被监听 ⇒ 不重复拉起,消除慢启动期双开互踩);
'      · 生命周期记录 logs/backend-lifecycle.log(启动/退出时间+退出码,
'        后端"无声死亡"从此有现场可查)。
' Keep this file pure ASCII (wscript reads .vbs with the ANSI code page).
' ============================================================
Option Explicit
Dim sh, fso, root, py, sp, srv
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
py = root & "\.venv\Scripts\python.exe"
sp = root & "\backend\.venv\Lib\site-packages"
srv = root & "\scripts\run_uvicorn_tracked.py"
If Not fso.FileExists(py) Then WScript.Quit 2
If Not fso.FileExists(srv) Then WScript.Quit 3

sh.Environment("PROCESS")("PYTHONPATH") = sp
sh.Environment("PROCESS")("NO_RELOAD") = "true"
sh.Environment("PROCESS")("DATA_CENTER_MODE") = "standalone"
sh.Environment("PROCESS")("BACKEND_PORT") = "8000"
sh.Environment("PROCESS")("BACKEND_HOST") = "0.0.0.0"
sh.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
sh.CurrentDirectory = root

sh.Run("""" & py & """ """ & srv & """", 1, False)
