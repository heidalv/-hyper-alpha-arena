' ============================================================
' Silent launcher: run an executable with arguments, NO window.
'
' WHY THIS EXISTS / WHY THE PREVIOUS ATTEMPT FAILED
'   First attempt chained:  task -> wscript -> run-hidden.vbs -> .bat -> powershell
'   The .bat step ALWAYS allocates a console (cmd.exe -> conhost.exe),
'   and that console window is exactly the black box that kept flashing.
'   Measured proof (process monitor, 2026-09-20 18:30:04):
'       cmd.exe    /c scripts\aster-depth-watchdog.bat
'       conhost.exe 0x4          <-- visible console window
'
'   KEY FACT: the hidden flag does NOT propagate through the chain.
'   WshShell.Run(cmd, 0, ..) only hides the window it creates DIRECTLY;
'   the .bat then starts cmd.exe with its own default (visible) console.
'
'   => Never route through cmd.exe / .bat for a hidden task.
'      Launch the FINAL process directly with window mode 0.
'
' USAGE (from a scheduled task)
'   wscript.exe //B //Nologo run-quiet.vbs <exe> [args...]
'
' Note: wscript.exe itself is a GUI-subsystem binary, so it never shows
'   a console; the only process created is the target, hidden.
'
' Keep this file pure ASCII (wscript reads .vbs with the ANSI code page).
' ============================================================
Option Explicit

Dim args, sh, exe, i, cmd
Set args = WScript.Arguments

If args.Count = 0 Then
    WScript.Quit 2
End If

exe = args(0)
cmd = """" & exe & """"
For i = 1 To args.Count - 1
    cmd = cmd & " " & args(i)
Next

Set sh = CreateObject("WScript.Shell")
' 0 = hidden, False = do not wait
sh.Run cmd, 0, False
WScript.Quit 0
