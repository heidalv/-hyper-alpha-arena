' ============================================================
' [h768b 2026-10-03] start-dc-hidden.vbs(基础解释器直启版)
'
' 实测根因(两轮排查):
'   ① `Start-Process -WindowStyle Hidden` 对控制台程序**不生效**(PowerShell 已知);
'   ② 改用 `WshShell.Run(cmd, 0)` 后**仍然弹窗** —— 因为 venv 的
'      `backend\.venv\Scripts\python.exe` 只是 **stub**,它会再拉起
'      `.runtime\Python312\python.exe` 作为**子进程**,而子进程**新分配一个可见控制台**
'      (实测:pids 33908(stub) → 10500(基础解释器),可见窗口挂在后者上)。
'
' 对策:直接启动**基础解释器**,用 PYTHONPATH 指向 venv 的 site-packages
'   (等价于 venv 环境,但不经 stub ⇒ 不会派生新控制台)。
'   pyvenv.cfg: home = .runtime\Python312,venv site-packages = backend\.venv\Lib\site-packages
' ============================================================
Option Explicit
Dim sh, fso, root, py, sp
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
py = root & "\.runtime\Python312\python.exe"
sp = root & "\backend\.venv\Lib\site-packages"
If Not fso.FileExists(py) Then WScript.Quit 2

sh.Environment("PROCESS")("PYTHONPATH") = sp
sh.Environment("PROCESS")("DATA_CENTER_MODE") = "standalone"
sh.Environment("PROCESS")("DATA_CENTER_PROCESS") = "1"
sh.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
sh.CurrentDirectory = root

sh.Run("""" & py & """ -m backend.workers.market_data_center", 0, False)
