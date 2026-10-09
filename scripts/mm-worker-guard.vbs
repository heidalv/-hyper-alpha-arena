' ============================================================
' MM lane worker guard: do NOT start a second worker if one is
' already alive and healthy.
'
' WHY THIS EXISTS  (2026-09-21, measured)
'   The scheduled task DSH_MM_WORKER has:
'       <Repetition><Interval>PT30M</Interval></Repetition>
'       <StopOnIdleEnd>true</StopOnIdleEnd>
'   i.e. it fires EVERY 30 MINUTES.  Measured consequences:
'
'     * 117 "start lane=mm_asterdex" lines in logs/mm_lane_worker.log
'     * most died 3s later with "another worker alive (pid=...) -> exit"
'       (IgnoreNew + the worker's own lock prevented double-ticking,
'        so this was NOT a double-write bug)
'     * but occasionally the previous process had just exited, so the
'       new one ACQUIRED the lock and started fresh:
'           ticks reset to 1, mid_hist rebuilt, F109 backfill re-ran
'       and every such restart emitted ~54 orphan flatten legs
'       (positions closed at taker cost because in-memory state was gone)
'
'   Root cause: a RUN-ONCE-AND-STAY-RESIDENT worker being launched on a
'   30-minute timer.  The repetition interval is the bug.
'
' WHY NOT JUST FIX THE TASK
'   `schtasks /Create /XML ... /F` returns "Access is denied" for this
'   task (needs elevation).  So the guard goes on the LAUNCH PATH
'   instead: this file is a plain script (no admin needed) and every
'   task-triggered start goes through it.
'
' LOGIC
'   1. Read logs/mm_lane_worker.lock (the worker writes its own PID).
'   2. Is that PID alive AND actually running mm_lane_worker?
'        YES -> exit 0, do not launch (the task run becomes a no-op)
'        NO  -> clear the stale lock, then launch normally
'   3. Never fail closed.  Any unexpected error => launch anyway.
'      A guard that blocks the worker is far worse than a stray process.
'
' USAGE (from a scheduled task)
'   wscript.exe //B //Nologo mm-worker-guard.vbs <exe> <worker_script>
'
' Keep this file pure ASCII (wscript reads .vbs with the ANSI code page).
' ============================================================
Option Explicit

Dim g_exe, g_worker, g_fso, g_sh, g_lockPath, g_reason

' ---- Is `pid` a live process running our worker script? ----
' Plain "PID exists" is not enough: PIDs get recycled, so a stale lock
' could match an unrelated process and block the worker forever.
Function PidIsOurWorker(pid)
    Dim wmi, procs, p, cl
    PidIsOurWorker = False
    If pid <= 0 Then Exit Function
    On Error Resume Next
    Set wmi = GetObject("winmgmts:\\.\root\cimv2")
    If Err.Number = 0 Then
        Set procs = wmi.ExecQuery("SELECT ProcessId, CommandLine FROM Win32_Process WHERE ProcessId = " & pid)
        If Err.Number = 0 Then
            For Each p In procs
                cl = ""
                If Not IsNull(p.CommandLine) Then cl = LCase(p.CommandLine)
                If InStr(cl, "mm_lane_worker") > 0 Then PidIsOurWorker = True
            Next
        End If
    End If
    On Error GoTo 0
End Function

Sub Main()
    Dim args, lockTxt, pid, ts
    Set args = WScript.Arguments
    If args.Count < 2 Then
        WScript.Quit 2
    End If
    g_exe = args(0)
    g_worker = args(1)

    Set g_fso = CreateObject("Scripting.FileSystemObject")
    ' repo root = parent of the scripts folder holding this file
    g_lockPath = g_fso.GetParentFolderName(g_fso.GetParentFolderName(WScript.ScriptFullName)) _
                 & "\logs\mm_lane_worker.lock"
    Set g_sh = CreateObject("WScript.Shell")

    pid = 0
    g_reason = "no lock file"

    On Error Resume Next
    If g_fso.FileExists(g_lockPath) Then
        Set ts = g_fso.OpenTextFile(g_lockPath, 1)
        lockTxt = Trim(ts.ReadAll)
        ts.Close
        If IsNumeric(lockTxt) Then pid = CLng(lockTxt)
    End If
    On Error GoTo 0

    If PidIsOurWorker(pid) Then
        ' A healthy worker is already ticking => this task run is a no-op.
        ' Deliberately silent: this fires every 30 min and logging every
        ' no-op would bury the real events in the worker log.
        WScript.Quit 0
    End If

    If pid > 0 Then g_reason = "stale lock (pid " & pid & " is not our worker)"
    If g_fso.FileExists(g_lockPath) Then
        On Error Resume Next
        g_fso.DeleteFile g_lockPath, True
        g_fso.DeleteFile g_lockPath & ".pid", True
        On Error GoTo 0
    End If

    g_sh.Run """" & g_exe & """ """ & g_worker & """", 0, False
    WScript.Quit 0
End Sub

Main
