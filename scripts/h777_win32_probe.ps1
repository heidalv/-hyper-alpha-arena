# [h777] REAL window visibility probe via Win32 EnumWindows, 100ms sampling.
# Catches sub-200ms console flashes that Get-Process sampling misses.
# ASCII only.
$ErrorActionPreference = 'Continue'
$src = @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class WinEnum {
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int max);
    public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
    public static List<string> VisibleConsoles() {
        var out2 = new List<string>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            uint pid; GetWindowThreadProcessId(h, out pid);
            var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
            string t = sb.ToString();
            if (t.Length == 0) t = "(no-title)";
            out2.Add(pid + "|" + t);
            return true;
        }, IntPtr.Zero);
        return out2;
    }
}
"@
Add-Type -TypeDefinition $src -ErrorAction SilentlyContinue
if (-not ('WinEnum' -as [type])) {
    Write-Host 'ERR: Add-Type failed - cannot use Win32 probe'
    exit 2
}
$knownPid = @{}
Get-Process -Name 'cmd','powershell','python','conhost','wscript' -ErrorAction SilentlyContinue | ForEach-Object { $knownPid[$_.Id] = $true }
$t0 = Get-Date
$hits = @()
while (((Get-Date) - $t0).TotalSeconds -lt 120) {
    $vis = [WinEnum]::VisibleConsoles()
    foreach ($v in $vis) {
        $parts = $v.Split('|', 2)
        $pid2 = [int]$parts[0]
        $title = $parts[1]
        # only report NEW processes (known PIDs are long-lived windows we already know)
        if ($knownPid.ContainsKey($pid2)) { continue }
        $proc = Get-Process -Id $pid2 -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $pn = $proc.ProcessName
        if ($pn -notmatch '^(cmd|powershell|python|conhost|wscript|csrss)$') { continue }
        $knownPid[$pid2] = $true
        $wmi = Get-CimInstance Win32_Process -Filter "ProcessId=$pid2" -ErrorAction SilentlyContinue
        $cmdline = '(n/a)'
        if ($wmi) { $cmdline = $wmi.CommandLine }
        $pp = '(exited)'
        if ($wmi -and $wmi.ParentProcessId) {
            $pw = Get-CimInstance Win32_Process -Filter "ProcessId=$($wmi.ParentProcessId)" -ErrorAction SilentlyContinue
            if ($pw) { $pp = $pw.Name + ' ' + $pw.CommandLine }
        }
        $line = (Get-Date -Format 'HH:mm:ss.fff') + ' VISIBLE ' + $pn + '[' + $pid2 + '] title="' + $title + '"'
        $line += ' || parent=' + $pp
        $line += ' || self=' + $cmdline
        $hits += $line
        Write-Host $line
    }
    Start-Sleep -Milliseconds 100
}
Write-Host ('total visible console windows caught in 120s: ' + $hits.Count)
