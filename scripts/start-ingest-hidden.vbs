' ============================================================
' [h772 2026-10-03] start-ingest-hidden.vbs —— 无窗口启动 asterdex 采集器
'
' WHY(用户实测"每分钟跳一次黑框"的**真凶**):
'   `aster-depth-watchdog.ps1` 的 `Start-Ingest()` 用
'       Start-Process -FilePath <venv python> ... -WindowStyle Hidden
'   而 **-WindowStyle Hidden 对控制台程序不生效**(PowerShell 已知行为)⇒
'   看门狗 interval=60s,每轮判定"ingest 不在跑"就拉一次 ⇒ **每分钟一个可见黑框**。
'
' 本脚本用 WScript.Shell.Run(cmd, 0, False) 的 SW_HIDE(对控制台程序有效),
' 并用 cmd /c 做日志重定向(cmd 的控制台同样被隐藏)。
' ============================================================
Option Explicit
Dim sh, fso, root, py, stby, logOut, logErr, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
py = root & "\.runtime\Python312\python.exe"
stby = root & "\scripts\standby_ingest.py"
logOut = root & "\logs\standby-ingest.out.log"
logErr = root & "\logs\standby-ingest.err.log"
If Not fso.FileExists(py) Then WScript.Quit 2
If Not fso.FileExists(stby) Then WScript.Quit 3

cmd = "cmd /c """"" & py & """ """ & stby & """ --apply --to-prod --seconds 0" _
      & " >> """ & logOut & """ 2>> """ & logErr & """"""

sh.Environment("PROCESS")("PYTHONPATH") = root & "\backend\.venv\Lib\site-packages"
sh.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
sh.CurrentDirectory = root

sh.Run cmd, 0, False
