Option Explicit
Dim fso, objShell, desktopPath, scriptDir, batPath, debugBatPath
Dim folder, file

Set objShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
desktopPath = objShell.SpecialFolders("Desktop")

' Find bat files - prefer non-debug version
Set folder = fso.GetFolder(scriptDir)
For Each file In folder.Files
    If InStr(1, file.Name, "AlphaArena", 1) > 0 And LCase(fso.GetExtensionName(file.Name)) = "bat" Then
        If InStr(1, file.Name, "debug", 1) > 0 Then
            debugBatPath = file.Path
        Else
            batPath = file.Path
        End If
    End If
Next

' Use non-debug if found, otherwise use debug
If IsEmpty(batPath) Or batPath = "" Then
    If Not IsEmpty(debugBatPath) And debugBatPath <> "" Then
        batPath = debugBatPath
    End If
End If

If IsEmpty(batPath) Or batPath = "" Then
    WScript.Echo "ERROR: Cannot find AlphaArena bat in " & scriptDir
    WScript.Quit 1
End If

Dim shortcutPath
shortcutPath = fso.BuildPath(desktopPath, "Alpha Arena.lnk")

Dim shortcut
Set shortcut = objShell.CreateShortcut(shortcutPath)
shortcut.TargetPath = batPath
shortcut.WorkingDirectory = scriptDir
shortcut.Description = "Alpha Arena"
shortcut.WindowStyle = 7
shortcut.Save()

WScript.Echo "OK" & vbCrLf & "Desktop: " & shortcutPath & vbCrLf & "Target: " & batPath
