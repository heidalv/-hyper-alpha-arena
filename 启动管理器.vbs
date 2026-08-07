' Hyper-Alpha-Arena Launcher - VBS 无窗口启动器
' 双击此文件启动管理器，完全无控制台窗口

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 获取脚本所在目录
scriptPath = fso.GetParentFolderName(WScript.ScriptFullName)
launcherPath = scriptPath & "\launcher.py"

' 优先使用虚拟环境的 pythonw
venvPythonw = scriptPath & "\backend\venv\Scripts\pythonw.exe"

If fso.FileExists(venvPythonw) Then
    pythonExe = venvPythonw
Else
    pythonExe = "pythonw"
End If

' 静默启动
WshShell.Run """" & pythonExe & """ """ & launcherPath & """", 0, False
