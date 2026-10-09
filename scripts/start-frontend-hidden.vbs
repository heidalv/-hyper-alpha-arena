' ============================================================
' [h773 2026-10-03] start-frontend-hidden.vbs —— 无窗口启动前端 dev server
'
' WHY: `start-frontend-guarded-quiet.ps1` 用
'   Start-Process -FilePath 'cmd.exe' -ArgumentList "/c cd /d <fe> && npm run dev >> log"
'   -WindowStyle Hidden
'   ⇒ **cmd.exe 一定分配控制台**,-WindowStyle Hidden 对控制台程序不生效 ⇒ 每次
'   后端/前端挂掉重启时弹一个黑框(实测日志:21:15 / 21:45 / 21:55 各一次)。
' 改用 WScript.Shell.Run(cmd, 0, False):SW_HIDE 对控制台程序有效。
' ============================================================
Option Explicit
Dim sh, fso, root, fe, logf, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
fe = root & "\frontend-next"
logf = root & "\logs\frontend-next.log"
If Not fso.FolderExists(fe) Then WScript.Quit 2

cmd = "cmd /c cd /d """ & fe & """ && npm run dev >> """ & logf & """ 2>&1"
sh.CurrentDirectory = fe
sh.Run cmd, 0, False
