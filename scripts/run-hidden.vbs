' ============================================================
' Silent launcher for scheduled tasks.
'
' WHY THIS FILE EXISTS
'   Tasks registered with schtasks /TR "powershell.exe -File x.ps1"
'   (or a .bat) run in the user's INTERACTIVE session and therefore
'   flash a console window every time they fire. With a 2-minute
'   watchdog that is a black box popping up all day.
'
'   WScript.Shell.Run(cmd, 0, False) is the reliable way to launch
'   with NO window at all -- neither -WindowStyle Hidden nor a
'   detached Start-Process suppresses the initial console flash.
'
' USAGE (from a scheduled task)
'   wscript.exe //B //Nologo run-hidden.vbs "<full path to .bat>"
'
' The //B flag = batch mode (no script error dialogs), //Nologo =
' no banner. Keep this file pure ASCII: wscript reads .vbs with the
' system ANSI code page, and non-ASCII bytes can break parsing.
' ============================================================
Option Explicit

Dim args, sh, target, i
Set args = WScript.Arguments

If args.Count = 0 Then
    WScript.Quit 2
End If

' Re-join arguments so paths containing spaces survive the task's
' quoting (the task passes the path as a single quoted argument, but
' defensive joining avoids a silent no-op if quoting drifts).
target = args(0)
For i = 1 To args.Count - 1
    target = target & " " & args(i)
Next

Set sh = CreateObject("WScript.Shell")
' 0 = hidden window, False = do not wait for completion
sh.Run """" & target & """", 0, False
WScript.Quit 0
