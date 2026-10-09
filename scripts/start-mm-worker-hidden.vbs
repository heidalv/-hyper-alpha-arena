' ============================================================
' [h769b 2026-10-03] start-mm-worker-hidden.vbs
'
' WHY: 最近几次重启做市 worker 时直接跑 `mm_lane_worker.py`,**绕过了
'   `mm_lane_worker.bat`** ⇒ 丢掉 `set MM_LANE_LIMITS_ENFORCE=1`
'   ⇒ **风险上限(净 0.5×/总 1.0×/单边 0.75×)完全没生效**。
'   实测代价:21:11 LYN 一笔 $1,392 名义(5× 权益)的腿 −85.8bp = **−11.95U**,
'   占当时段亏损的绝大部分(权益 279 → 265)。
'
' 本脚本等价复刻 .bat 的关键环境,但不经 cmd.exe / venv stub(两者都会弹黑框):
'   PYTHONIOENCODING=utf-8、MM_LANE_LIMITS_ENFORCE=1
'   直启 .runtime\Python312\python.exe + PYTHONPATH=venv site-packages。
'
' ⚠️ 今后重启做市 worker 一律用本脚本(或 .bat),**不要**直接 Start-Process python。
' ============================================================
Option Explicit
Dim sh, fso, root, py, sp, worker, logf
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
py = root & "\.runtime\Python312\python.exe"
sp = root & "\backend\.venv\Lib\site-packages"
worker = root & "\scripts\mm_lane_worker.py"
If Not fso.FileExists(py) Then WScript.Quit 2
If Not fso.FileExists(worker) Then WScript.Quit 3

sh.Environment("PROCESS")("PYTHONPATH") = sp
sh.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
sh.Environment("PROCESS")("MM_LANE_LIMITS_ENFORCE") = "1"
sh.CurrentDirectory = root

' 用 cmd 重定向到日志会引入控制台 ⇒ 改由 worker 自己写 logs\mm_lane_worker.out.log
' (worker 内部已用 logging;此处不再重定向,保持无窗口)
sh.Run("""" & py & """ """ & worker & """", 0, False)
