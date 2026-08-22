' Hyper-Alpha-Arena Launcher - VBS 无窗口启动器
' 双击此文件启动管理器，完全无控制台窗口
' [2026-08-22 修复] 旧版写死 backend\venv（无点目录，从未存在），
' 回退到系统 pythonw（可能是错误的 Python）。真实 venv 是 backend\.venv（带点）。

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 获取脚本所在目录
scriptPath = fso.GetParentFolderName(WScript.ScriptFullName)
launcherPath = scriptPath & "\launcher.py"

' 优先使用虚拟环境的 pythonw（带点的 .venv 才是真实目录）
venvPythonw = scriptPath & "\backend\.venv\Scripts\pythonw.exe"

If fso.FileExists(venvPythonw) Then
    pythonExe = venvPythonw
Else
    ' 兜底：项目根 .venv（与 start-backend-fix.cmd 同源）
    rootVenvPythonw = scriptPath & "\.venv\Scripts\pythonw.exe"
    If fso.FileExists(rootVenvPythonw) Then
        pythonExe = rootVenvPythonw
    Else
        pythonExe = "pythonw"
    End If
End If

' 静默启动
WshShell.Run """" & pythonExe & """ """ & launcherPath & """", 0, False
